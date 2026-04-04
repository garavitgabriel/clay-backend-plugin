"""Hosted webhook service — deploy once, get a permanent URL.

A small FastAPI service that:
1. Accepts webhooks from Clay (POST /webhook)
2. Stores records in PostgreSQL
3. Exposes a REST API for the MCP plugin to query remotely
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request

DATABASE_URL = os.environ.get("DATABASE_URL", "")
API_KEY = os.environ.get("API_KEY", "")

pool: asyncpg.Pool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    await _init_db()

    # Start scheduler if SCHEDULES_PATH or ANTHROPIC_API_KEY is set
    scheduler = None
    schedules_path = os.environ.get("SCHEDULES_PATH", "")
    if schedules_path and os.environ.get("ANTHROPIC_API_KEY"):
        from scheduler import start_scheduler

        scheduler = start_scheduler(pool, schedules_path)

    yield

    if scheduler:
        scheduler.shutdown(wait=False)
    await pool.close()


app = FastAPI(title="Clay Backend — Hosted Webhook Service", lifespan=lifespan)


# --- Auth ---


def verify_api_key(
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None),
):
    if not API_KEY:
        return
    if authorization == f"Bearer {API_KEY}":
        return
    if x_api_key == API_KEY:
        return
    raise HTTPException(status_code=401, detail="Unauthorized")


# --- DB Init ---


async def _init_db():
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS analysis_records (
                id              TEXT PRIMARY KEY,
                record_id       TEXT NOT NULL,
                analysis_type   TEXT NOT NULL,
                data            JSONB NOT NULL,
                source          TEXT,
                entity_id       TEXT,
                entity_name     TEXT,
                tags            JSONB DEFAULT '[]',
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (record_id, analysis_type)
            );

            CREATE INDEX IF NOT EXISTS idx_analysis_type
                ON analysis_records(analysis_type);
            CREATE INDEX IF NOT EXISTS idx_entity_id
                ON analysis_records(entity_id);
            CREATE INDEX IF NOT EXISTS idx_created_at
                ON analysis_records(created_at);
            CREATE INDEX IF NOT EXISTS idx_data
                ON analysis_records USING GIN(data);
        """)


# --- Helpers ---


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _upsert_record(conn, rec: dict) -> str:
    """Upsert a single record. Returns 'ingested' or 'updated'."""
    record_uuid = str(uuid.uuid4())
    now = _now_iso()

    result = await conn.fetchrow(
        """
        INSERT INTO analysis_records
            (id, record_id, analysis_type, data, source,
             entity_id, entity_name, tags, created_at, received_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (record_id, analysis_type) DO UPDATE SET
            data = EXCLUDED.data,
            source = EXCLUDED.source,
            entity_id = EXCLUDED.entity_id,
            entity_name = EXCLUDED.entity_name,
            tags = EXCLUDED.tags,
            received_at = EXCLUDED.received_at
        RETURNING (xmax = 0) AS inserted
        """,
        record_uuid,
        rec["record_id"],
        rec["analysis_type"],
        json.dumps(rec.get("data", {})),
        rec.get("source"),
        rec.get("entity_id"),
        rec.get("entity_name"),
        json.dumps(rec.get("tags", [])),
        now,
        now,
    )
    return "ingested" if result["inserted"] else "updated"


# --- Endpoints ---


@app.get("/health")
async def health():
    return {"status": "ok", "service": "clay-backend-hosted"}


@app.post("/webhook", dependencies=[Depends(verify_api_key)])
async def webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if isinstance(body, list):
        raw_records = body
    elif isinstance(body, dict):
        raw_records = body.get("records", [body] if "record_id" in body else [])
    else:
        raise HTTPException(status_code=400, detail="Expected JSON object or array")

    if not raw_records:
        raise HTTPException(status_code=400, detail="No records found in payload")

    ingested = 0
    updated = 0
    errors = []

    async with pool.acquire() as conn:
        for i, rec in enumerate(raw_records):
            try:
                if "record_id" not in rec or "analysis_type" not in rec:
                    errors.append(f"Record {i}: missing record_id or analysis_type")
                    continue
                status = await _upsert_record(conn, rec)
                if status == "ingested":
                    ingested += 1
                else:
                    updated += 1
            except Exception as e:
                errors.append(f"Record {i}: {e}")

    return {"ingested": ingested, "updated": updated, "errors": errors}


@app.get("/api/v1/records", dependencies=[Depends(verify_api_key)])
async def list_records(
    analysis_type: str | None = None,
    entity_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    search_data: str | None = None,
    tags: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    conditions = []
    params = []
    idx = 1

    if analysis_type:
        conditions.append(f"analysis_type = ${idx}")
        params.append(analysis_type)
        idx += 1
    if entity_id:
        conditions.append(f"entity_id = ${idx}")
        params.append(entity_id)
        idx += 1
    if since:
        conditions.append(f"created_at >= ${idx}")
        params.append(since)
        idx += 1
    if until:
        conditions.append(f"created_at <= ${idx}")
        params.append(until)
        idx += 1
    if search_data:
        conditions.append(f"data::text ILIKE ${idx}")
        params.append(f"%{search_data}%")
        idx += 1
    if tags:
        for tag in tags.split(","):
            conditions.append(f"tags @> ${idx}::jsonb")
            params.append(json.dumps([tag.strip()]))
            idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    params.extend([limit, offset])
    query = f"""
        SELECT id, record_id, analysis_type, data, source,
               entity_id, entity_name, tags, created_at, received_at
        FROM analysis_records {where}
        ORDER BY created_at DESC
        LIMIT ${idx} OFFSET ${idx + 1}
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    return [
        {
            "id": r["id"],
            "record_id": r["record_id"],
            "analysis_type": r["analysis_type"],
            "data": json.loads(r["data"]) if isinstance(r["data"], str) else r["data"],
            "source": r["source"],
            "entity_id": r["entity_id"],
            "entity_name": r["entity_name"],
            "tags": json.loads(r["tags"]) if isinstance(r["tags"], str) else r["tags"],
            "created_at": r["created_at"].isoformat(),
            "received_at": r["received_at"].isoformat(),
        }
        for r in rows
    ]


@app.get("/api/v1/records/{record_id}", dependencies=[Depends(verify_api_key)])
async def get_record(record_id: str):
    async with pool.acquire() as conn:
        r = await conn.fetchrow(
            "SELECT * FROM analysis_records WHERE id = $1", record_id
        )
    if not r:
        raise HTTPException(status_code=404, detail="Record not found")
    return {
        "id": r["id"],
        "record_id": r["record_id"],
        "analysis_type": r["analysis_type"],
        "data": json.loads(r["data"]) if isinstance(r["data"], str) else r["data"],
        "source": r["source"],
        "entity_id": r["entity_id"],
        "entity_name": r["entity_name"],
        "tags": json.loads(r["tags"]) if isinstance(r["tags"], str) else r["tags"],
        "created_at": r["created_at"].isoformat(),
        "received_at": r["received_at"].isoformat(),
    }


@app.get("/api/v1/analysis-types", dependencies=[Depends(verify_api_key)])
async def list_analysis_types():
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT analysis_type, COUNT(*) as count,
                   MAX(created_at) as newest_record,
                   MIN(created_at) as oldest_record
            FROM analysis_records
            GROUP BY analysis_type
            ORDER BY count DESC
        """)
    if not rows:
        return []
    return [
        {
            "analysis_type": r["analysis_type"],
            "count": r["count"],
            "newest_record": r["newest_record"].isoformat(),
            "oldest_record": r["oldest_record"].isoformat(),
        }
        for r in rows
    ]


@app.get("/api/v1/analytics", dependencies=[Depends(verify_api_key)])
async def get_analytics(
    analysis_type: str | None = None,
    since: str | None = None,
):
    conditions = []
    params = []
    idx = 1

    if analysis_type:
        conditions.append(f"analysis_type = ${idx}")
        params.append(analysis_type)
        idx += 1
    if since:
        conditions.append(f"created_at >= ${idx}")
        params.append(since)
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM analysis_records {where}", *params
        )
        types = await conn.fetch(
            f"""
            SELECT analysis_type, COUNT(*) as count,
                   MAX(created_at) as newest_record,
                   MIN(created_at) as oldest_record
            FROM analysis_records {where}
            GROUP BY analysis_type ORDER BY count DESC
            """,
            *params,
        )

        entity_where = where
        if where:
            entity_where += " AND entity_id IS NOT NULL"
        else:
            entity_where = "WHERE entity_id IS NOT NULL"

        top_entities = await conn.fetch(
            f"""
            SELECT entity_id, entity_name, COUNT(*) as count
            FROM analysis_records {entity_where}
            GROUP BY entity_id, entity_name
            ORDER BY count DESC LIMIT 10
            """,
            *params,
        )

    return {
        "total_records": total,
        "records_by_type": [
            {
                "analysis_type": r["analysis_type"],
                "count": r["count"],
                "newest_record": r["newest_record"].isoformat(),
                "oldest_record": r["oldest_record"].isoformat(),
            }
            for r in types
        ],
        "top_entities": [
            {
                "entity_id": r["entity_id"],
                "entity_name": r["entity_name"],
                "count": r["count"],
            }
            for r in top_entities
        ],
    }


@app.delete("/api/v1/records", dependencies=[Depends(verify_api_key)])
async def delete_records(
    analysis_type: str | None = None,
    older_than: str | None = None,
    record_ids: str | None = None,
):
    conditions = []
    params = []
    idx = 1

    if analysis_type:
        conditions.append(f"analysis_type = ${idx}")
        params.append(analysis_type)
        idx += 1
    if older_than:
        conditions.append(f"created_at < ${idx}")
        params.append(older_than)
        idx += 1
    if record_ids:
        ids = [rid.strip() for rid in record_ids.split(",")]
        placeholders = ", ".join(f"${idx + i}" for i in range(len(ids)))
        conditions.append(f"id IN ({placeholders})")
        params.extend(ids)
        idx += len(ids)

    if not conditions:
        raise HTTPException(
            status_code=400, detail="At least one filter required"
        )

    where = f"WHERE {' AND '.join(conditions)}"
    async with pool.acquire() as conn:
        result = await conn.execute(
            f"DELETE FROM analysis_records {where}", *params
        )
    count = int(result.split()[-1])
    return {"deleted": count}
