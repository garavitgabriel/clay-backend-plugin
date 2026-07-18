"""Generate synthetic Clay-style fixtures.

Produces two analysis_types that share entity_ids so cross-type entity joins
are testable:

  - call_analysis      (per sales call, scored, tagged, with a summary)
  - company_enrichment (per company, firmographic enrichment)

Both reference the same set of companies via entity_id (e.g. "company-3"),
so a single entity has BOTH a call_analysis row and a company_enrichment row.

Outputs:
  tests/fixtures/clay_records.json   -> list[RecordInput-shaped dicts]
  tests/fixtures/call_analysis.csv   -> flat CSV for ingest_csv coverage

Re-run with:  python tests/fixtures/generate_fixtures.py
Deterministic (fixed seed) so the suite is reproducible.
"""

from __future__ import annotations

import csv
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 1337
N_COMPANIES = 24
HERE = Path(__file__).parent

COMPANIES = [
    "Acme Robotics", "Northwind Trading", "Globex Systems", "Initech Software",
    "Umbrella Health", "Stark Industries", "Wayne Logistics", "Wonka Foods",
    "Soylent Nutrition", "Hooli Cloud", "Pied Piper Data", "Vandelay Imports",
    "Cyberdyne AI", "Tyrell Bioworks", "Massive Dynamic", "Oscorp Materials",
    "Gekko Capital", "Bluth Property", "Dunder Mifflin", "Sterling Cooper",
    "Prestige Worldwide", "Vehement Capital", "Aperture Labs", "Black Mesa",
]

REPS = ["Alice Chen", "Bob Diaz", "Carmen Ruiz", "Dev Patel", "Erin Walsh"]
INDUSTRIES = ["SaaS", "Healthcare", "FinTech", "Manufacturing", "Logistics", "Biotech"]
SIZES = ["1-50", "51-200", "201-1000", "1000-5000", "5000+"]

CALL_TAGS = [
    ["budget_discussed", "champion_identified"],
    ["no_budget", "early_stage"],
    ["competitor_mentioned", "pricing_objection"],
    ["champion_identified", "next_steps_set"],
    ["ghosting_risk", "no_next_steps"],
    ["technical_deep_dive", "champion_identified"],
]

CALL_SUMMARIES = [
    "Strong discovery call. Prospect confirmed budget and named an executive sponsor. "
    "Clear pain around manual data entry; wants a pilot within the quarter.",
    "Early-stage conversation. No budget allocated yet and timeline is vague. "
    "Rep should nurture rather than push for a demo.",
    "Competitor came up repeatedly and the prospect pushed back hard on pricing. "
    "Deal at risk unless we can show ROI quickly.",
    "Great call — champion is engaged, next steps are set, and a security review is scheduled. "
    "Budget was discussed openly and is approved.",
    "Prospect went quiet on next steps and avoided commitment. High ghosting risk; "
    "no follow-up meeting was booked.",
    "Deep technical dive into the API and embeddings pipeline. The champion understands "
    "the integration and wants to involve their data team next.",
]

ENRICH_SUMMARIES = [
    "Mid-market SaaS company growing headcount in engineering; strong fit for our ICP.",
    "Healthcare provider with strict compliance needs; longer sales cycle expected.",
    "FinTech scale-up, well funded, actively buying tooling this fiscal year.",
    "Legacy manufacturer modernizing its data stack; budget owner is the new CTO.",
    "Logistics firm with thin margins; price sensitivity is high.",
    "Biotech research org with grant-driven, lumpy purchasing patterns.",
]


def _iso(dt: datetime) -> str:
    return dt.replace(tzinfo=timezone.utc).isoformat()


def generate() -> list[dict]:
    rng = random.Random(SEED)
    records: list[dict] = []
    base = datetime(2026, 5, 1, 9, 0, 0)

    # One call_analysis row per company (24)
    for i in range(N_COMPANIES):
        company = COMPANIES[i]
        entity_id = f"company-{i}"
        idx = i % len(CALL_SUMMARIES)
        score = rng.randint(35, 98)
        records.append({
            "record_id": f"call-{i:03d}",
            "analysis_type": "call_analysis",
            "source": "gong",
            "entity_id": entity_id,
            "entity_name": company,
            "tags": CALL_TAGS[idx],
            "data": {
                "rep": rng.choice(REPS),
                "score": score,
                "sentiment": (
                    "positive" if score >= 70 else ("neutral" if score >= 50 else "negative")
                ),
                "summary": CALL_SUMMARIES[idx],
                "duration_min": rng.randint(12, 58),
                "next_steps": idx not in (1, 4),
            },
        })

    # One company_enrichment row per company (24) — SAME entity_ids
    for i in range(N_COMPANIES):
        company = COMPANIES[i]
        entity_id = f"company-{i}"
        idx = i % len(ENRICH_SUMMARIES)
        records.append({
            "record_id": f"enrich-{i:03d}",
            "analysis_type": "company_enrichment",
            "source": "clearbit",
            "entity_id": entity_id,
            "entity_name": company,
            "tags": ["icp_fit"] if idx in (0, 2) else ["icp_review"],
            "data": {
                "industry": INDUSTRIES[i % len(INDUSTRIES)],
                "employee_range": SIZES[i % len(SIZES)],
                "fit_score": rng.randint(20, 95),
                "summary": ENRICH_SUMMARIES[idx],
                "funded": rng.choice([True, False]),
            },
        })

    # Stamp created_at across a 20-day window so since/until filters are meaningful.
    for n, rec in enumerate(records):
        rec["_created_at"] = _iso(base + timedelta(days=n % 20, hours=n % 7))

    return records


def write_json(records: list[dict]) -> Path:
    path = HERE / "clay_records.json"
    path.write_text(json.dumps(records, indent=2))
    return path


def write_csv(records: list[dict]) -> Path:
    """Flat CSV of just the call_analysis rows, for ingest_csv coverage."""
    path = HERE / "call_analysis.csv"
    calls = [r for r in records if r["analysis_type"] == "call_analysis"]
    fieldnames = ["Row ID", "Company", "Entity ID", "Rep", "Score", "Sentiment", "Summary"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in calls:
            d = r["data"]
            w.writerow({
                "Row ID": r["record_id"],
                "Company": r["entity_name"],
                "Entity ID": r["entity_id"],
                "Rep": d["rep"],
                "Score": d["score"],
                "Sentiment": d["sentiment"],
                "Summary": d["summary"],
            })
    return path


if __name__ == "__main__":
    recs = generate()
    jp = write_json(recs)
    cp = write_csv(recs)
    print(f"Wrote {len(recs)} records -> {jp}")
    print(f"Wrote CSV -> {cp}")
