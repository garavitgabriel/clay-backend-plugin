"""Scheduled skill execution — runs skills on cron and delivers results.

Reads schedules.yaml, loads the referenced SKILL.md as the prompt,
fetches records matching the context, calls the Anthropic API,
and sends the output to configured destinations (Slack, Clay, etc).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
import httpx
import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .services import record_service

logger = logging.getLogger(__name__)

SCHEDULES_PATH = os.environ.get("SCHEDULES_PATH", "schedules.yaml")
SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"

# Default model for scheduled synthesis. Override per-schedule with `model:`
# in schedules.yaml, or globally via CLAY_SCHEDULER_MODEL.
DEFAULT_MODEL = os.environ.get("CLAY_SCHEDULER_MODEL", "claude-sonnet-5")

# Rough guard against overflowing the model's context window: serialized records
# beyond this many characters are dropped (most-recent kept) and the synthesis
# prompt is told how many were omitted. ~4 chars/token → ~100k tokens of records.
PROMPT_CHAR_BUDGET = int(os.environ.get("CLAY_PROMPT_CHAR_BUDGET", "400000"))


def _load_skill(skill_name: str) -> str:
    """Load a SKILL.md file and return the body (without frontmatter)."""
    skill_path = SKILLS_DIR / skill_name / "SKILL.md"
    if not skill_path.exists():
        raise FileNotFoundError(f"Skill not found: {skill_path}")

    content = skill_path.read_text()

    # Strip YAML frontmatter
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            content = content[end + 3:].strip()

    return content


def _resolve_since(since_str: str) -> str:
    """Resolve relative time strings like '7 days ago' to ISO dates."""
    now = datetime.now(timezone.utc)
    parts = since_str.lower().strip().split()

    if len(parts) >= 3 and parts[-1] == "ago":
        try:
            amount = int(parts[0])
        except ValueError:
            return since_str

        unit = parts[1].rstrip("s")  # "days" -> "day"
        if unit == "day":
            return (now - timedelta(days=amount)).isoformat()
        elif unit == "hour":
            return (now - timedelta(hours=amount)).isoformat()
        elif unit == "week":
            return (now - timedelta(weeks=amount)).isoformat()
        elif unit == "month":
            return (now - timedelta(days=amount * 30)).isoformat()

    return since_str


def _fetch_records(context: dict) -> list[dict]:
    """Fetch records based on schedule context."""
    since = context.get("since")
    if since:
        since = _resolve_since(since)

    limit = context.get("limit", 100)
    records = record_service.query_records(
        analysis_type=context.get("analysis_type"),
        entity_id=context.get("entity_id"),
        tags=context.get("tags"),
        since=since,
        until=context.get("until"),
        limit=limit,
    )
    if len(records) >= min(limit, 200):
        logger.warning(
            "Fetch cap hit (%d records) — older records in the window were NOT "
            "fetched and the synthesis will only cover the most recent ones. "
            "Raise context.limit (max 200) or narrow the time window.",
            len(records),
        )
    return [r.model_dump() for r in records]


def _call_anthropic(skill_prompt: str, records: list[dict], schedule: dict) -> str:
    """Call the Anthropic API with the skill prompt and records."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set — required for scheduled synthesis")

    model = schedule.get("model", DEFAULT_MODEL)
    context = schedule.get("context", {})

    # Serialize records, dropping the oldest if they blow the prompt budget.
    # Records arrive most-recent-first from query_records.
    included = list(records)
    records_text = json.dumps(included, indent=2, default=str)
    omitted = 0
    while len(records_text) > PROMPT_CHAR_BUDGET and len(included) > 1:
        # Drop in chunks proportional to the overshoot to avoid O(n^2) re-dumps
        overshoot = len(records_text) / PROMPT_CHAR_BUDGET
        drop = max(1, int(len(included) * (1 - 1 / overshoot)))
        included = included[: len(included) - drop]
        omitted = len(records) - len(included)
        records_text = json.dumps(included, indent=2, default=str)
    if omitted:
        logger.warning(
            "Prompt budget (%d chars) exceeded — %d of %d records omitted from "
            "the synthesis prompt (oldest dropped). Narrow the schedule window "
            "or lower context.limit for full coverage.",
            PROMPT_CHAR_BUDGET, omitted, len(records),
        )

    user_message = (
        f"Analysis type: {context.get('analysis_type', 'all')}\n"
        f"Time period: {context.get('since', 'all time')}\n"
        f"Record count: {len(included)}"
        + (
            f" (NOTE: {omitted} additional matching records were omitted to fit "
            f"the prompt budget — state this limitation in your analysis)"
            if omitted else ""
        )
        + f"\n\nRecords:\n{records_text}"
    )

    # Add any custom prompt from the schedule config
    extra_prompt = schedule.get("prompt")
    if extra_prompt:
        user_message += f"\n\nAdditional instructions:\n{extra_prompt}"

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=schedule.get("max_tokens", 8192),
        system=skill_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    return response.content[0].text


def _send_to_slack(webhook_url: str, text: str, schedule_name: str) -> None:
    """Send formatted message to Slack incoming webhook."""
    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Clay Analysis: {schedule_name}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": text[:3000],  # Slack block text limit
                },
            },
        ],
    }

    # If text is longer than 3000 chars, add continuation blocks
    remaining = text[3000:]
    while remaining:
        payload["blocks"].append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": remaining[:3000],
            },
        })
        remaining = remaining[3000:]

    resp = httpx.post(webhook_url, json=payload, timeout=10)
    if resp.status_code != 200:
        logger.error(f"Slack delivery failed: {resp.status_code} {resp.text}")
    else:
        logger.info(f"Slack delivery successful for '{schedule_name}'")


def _send_to_clay_webhook(
    url: str, text: str, schedule_name: str, headers: dict | None = None,
) -> None:
    """Send synthesis result back to a Clay webhook table."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    payload = {
        "record_id": f"synthesis-{schedule_name}-{ts}",
        "analysis_type": "synthesis",
        "data": {
            "schedule_name": schedule_name,
            "synthesis": text,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }

    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)

    resp = httpx.post(url, json=payload, headers=req_headers, timeout=10)
    if resp.status_code != 200:
        logger.error(f"Clay webhook delivery failed: {resp.status_code} {resp.text}")
    else:
        logger.info(f"Clay webhook delivery successful for '{schedule_name}'")


def _send_outputs(text: str, outputs: list[dict], schedule_name: str) -> None:
    """Route synthesis result to all configured outputs."""
    for output in outputs:
        output_type = output.get("type", "")
        try:
            if output_type == "slack":
                _send_to_slack(output["webhook_url"], text, schedule_name)
            elif output_type == "clay_webhook":
                _send_to_clay_webhook(
                    output["url"], text, schedule_name,
                    headers=output.get("headers"),
                )
            else:
                logger.warning(f"Unknown output type: {output_type}")
        except Exception as e:
            logger.error(f"Output delivery failed ({output_type}): {e}")


def _run_schedule(schedule: dict) -> None:
    """Execute a single scheduled analysis."""
    name = schedule["name"]
    skill_name = schedule["skill"]
    context = schedule.get("context", {})
    outputs = schedule.get("outputs", [])

    logger.info(f"Running scheduled analysis: {name}")

    try:
        # Load skill
        skill_prompt = _load_skill(skill_name)

        # Fetch records
        records = _fetch_records(context)
        if not records:
            logger.info(f"No records found for '{name}', skipping")
            return

        logger.info(f"Fetched {len(records)} records for '{name}'")

        # Call Anthropic API
        result = _call_anthropic(skill_prompt, records, schedule)

        # Deliver outputs
        _send_outputs(result, outputs, name)

        logger.info(f"Completed scheduled analysis: {name}")

    except Exception as e:
        logger.error(f"Scheduled analysis '{name}' failed: {e}")


def load_schedules(path: str | None = None) -> list[dict]:
    """Load schedule configurations from YAML file."""
    path = path or SCHEDULES_PATH
    if not os.path.exists(path):
        return []

    with open(path) as f:
        config = yaml.safe_load(f)

    return config.get("schedules", []) if config else []


def start_scheduler(schedules_path: str | None = None) -> BackgroundScheduler | None:
    """Start the APScheduler with all configured schedules.

    Returns the scheduler instance, or None if no schedules configured.
    """
    schedules = load_schedules(schedules_path)
    if not schedules:
        logger.info("No schedules configured")
        return None

    scheduler = BackgroundScheduler()

    for schedule in schedules:
        name = schedule.get("name", "unnamed")
        cron_expr = schedule.get("cron", "")

        if not cron_expr:
            logger.warning(f"Schedule '{name}' has no cron expression, skipping")
            continue

        # Parse cron expression (minute hour day month day_of_week)
        parts = cron_expr.split()
        if len(parts) != 5:
            logger.warning(f"Invalid cron for '{name}': {cron_expr}")
            continue

        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )

        scheduler.add_job(
            _run_schedule,
            trigger=trigger,
            args=[schedule],
            id=name,
            name=name,
        )
        logger.info(f"Scheduled '{name}' with cron: {cron_expr}")

    scheduler.start()
    logger.info(f"Scheduler started with {len(schedules)} job(s)")
    return scheduler
