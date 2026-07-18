"""Hosted scheduler — runs skills on cron against PostgreSQL.

Same concept as the local scheduler but queries PostgreSQL
and runs inside the hosted FastAPI service.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
import asyncpg
import httpx
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent.parent / "skills"


def _load_skill(skill_name: str) -> str:
    """Load a SKILL.md file and return the body (without frontmatter)."""
    skill_path = SKILLS_DIR / skill_name / "SKILL.md"
    if not skill_path.exists():
        raise FileNotFoundError(f"Skill not found: {skill_path}")

    content = skill_path.read_text()
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
        unit = parts[1].rstrip("s")
        if unit == "day":
            return (now - timedelta(days=amount)).isoformat()
        elif unit == "hour":
            return (now - timedelta(hours=amount)).isoformat()
        elif unit == "week":
            return (now - timedelta(weeks=amount)).isoformat()
        elif unit == "month":
            return (now - timedelta(days=amount * 30)).isoformat()

    return since_str


async def _fetch_records(pool: asyncpg.Pool, context: dict) -> list[dict]:
    """Fetch records from PostgreSQL."""
    conditions = []
    params = []
    idx = 1

    if context.get("analysis_type"):
        conditions.append(f"analysis_type = ${idx}")
        params.append(context["analysis_type"])
        idx += 1

    since = context.get("since")
    if since:
        conditions.append(f"created_at >= ${idx}")
        params.append(_resolve_since(since))
        idx += 1

    if context.get("entity_id"):
        conditions.append(f"entity_id = ${idx}")
        params.append(context["entity_id"])
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    limit = context.get("limit", 100)
    params.extend([limit])

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, record_id, analysis_type, data, source,
                   entity_id, entity_name, tags, created_at
            FROM analysis_records {where}
            ORDER BY created_at DESC
            LIMIT ${idx}
            """,
            *params,
        )

    return [
        {
            "id": r["id"],
            "record_id": r["record_id"],
            "analysis_type": r["analysis_type"],
            "data": json.loads(r["data"]) if isinstance(r["data"], str) else r["data"],
            "entity_id": r["entity_id"],
            "entity_name": r["entity_name"],
            "tags": json.loads(r["tags"]) if isinstance(r["tags"], str) else r["tags"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


def _call_anthropic(
    skill_prompt: str, records: list[dict], schedule: dict,
) -> str:
    """Call the Anthropic API with the skill prompt and records."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set")

    model = schedule.get("model", os.environ.get("CLAY_SCHEDULER_MODEL", "claude-sonnet-5"))
    context = schedule.get("context", {})

    records_text = json.dumps(records, indent=2, default=str)
    user_message = (
        f"Analysis type: {context.get('analysis_type', 'all')}\n"
        f"Time period: {context.get('since', 'all time')}\n"
        f"Record count: {len(records)}\n\n"
        f"Records:\n{records_text}"
    )

    extra_prompt = schedule.get("prompt")
    if extra_prompt:
        user_message += f"\n\nAdditional instructions:\n{extra_prompt}"

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=skill_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text


def _send_to_slack(webhook_url: str, text: str, name: str) -> None:
    """Send to Slack incoming webhook."""
    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"Clay Analysis: {name}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text[:3000]},
            },
        ],
    }
    remaining = text[3000:]
    while remaining:
        payload["blocks"].append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": remaining[:3000]},
        })
        remaining = remaining[3000:]

    resp = httpx.post(webhook_url, json=payload, timeout=10)
    if resp.status_code != 200:
        logger.error(f"Slack failed: {resp.status_code} {resp.text}")
    else:
        logger.info(f"Slack delivered: {name}")


def _send_to_clay_webhook(
    url: str, text: str, name: str, headers: dict | None = None,
) -> None:
    """Send synthesis result to a Clay webhook table."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    payload = {
        "record_id": f"synthesis-{name}-{ts}",
        "analysis_type": "synthesis",
        "data": {
            "schedule_name": name,
            "synthesis": text,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)

    resp = httpx.post(url, json=payload, headers=req_headers, timeout=10)
    if resp.status_code != 200:
        logger.error(f"Clay webhook failed: {resp.status_code} {resp.text}")
    else:
        logger.info(f"Clay webhook delivered: {name}")


async def _run_schedule(pool: asyncpg.Pool, schedule: dict) -> None:
    """Execute a single scheduled analysis."""
    name = schedule["name"]
    skill_name = schedule["skill"]
    context = schedule.get("context", {})
    outputs = schedule.get("outputs", [])

    logger.info(f"Running: {name}")

    try:
        skill_prompt = _load_skill(skill_name)
        records = await _fetch_records(pool, context)

        if not records:
            logger.info(f"No records for '{name}', skipping")
            return

        logger.info(f"Fetched {len(records)} records for '{name}'")

        result = _call_anthropic(skill_prompt, records, schedule)

        for output in outputs:
            try:
                if output["type"] == "slack":
                    _send_to_slack(output["webhook_url"], result, name)
                elif output["type"] == "clay_webhook":
                    _send_to_clay_webhook(
                        output["url"], result, name, output.get("headers"),
                    )
            except Exception as e:
                logger.error(f"Output failed ({output['type']}): {e}")

        logger.info(f"Completed: {name}")

    except Exception as e:
        logger.error(f"Schedule '{name}' failed: {e}")


def load_schedules(path: str | None = None) -> list[dict]:
    """Load schedule configs from YAML."""
    path = path or os.environ.get("SCHEDULES_PATH", "schedules.yaml")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        config = yaml.safe_load(f)
    return config.get("schedules", []) if config else []


def start_scheduler(
    pool: asyncpg.Pool, schedules_path: str | None = None,
) -> AsyncIOScheduler | None:
    """Start the async scheduler with all configured jobs."""
    schedules = load_schedules(schedules_path)
    if not schedules:
        logger.info("No schedules configured")
        return None

    scheduler = AsyncIOScheduler()

    for schedule in schedules:
        name = schedule.get("name", "unnamed")
        cron_expr = schedule.get("cron", "")
        if not cron_expr:
            continue

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
            args=[pool, schedule],
            id=name,
            name=name,
        )
        logger.info(f"Scheduled '{name}': {cron_expr}")

    scheduler.start()
    logger.info(f"Scheduler started: {len(schedules)} job(s)")
    return scheduler
