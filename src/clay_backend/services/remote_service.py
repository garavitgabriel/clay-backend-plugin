"""Remote service — calls the hosted API instead of local SQLite."""

from __future__ import annotations

import os

import httpx


def _base_url() -> str:
    url = os.environ.get("REMOTE_URL", "").rstrip("/")
    if not url:
        raise RuntimeError("REMOTE_URL not set")
    return url


def _headers() -> dict:
    api_key = os.environ.get("REMOTE_API_KEY", "")
    if api_key:
        return {"Authorization": f"Bearer {api_key}"}
    return {}


def _client() -> httpx.Client:
    return httpx.Client(base_url=_base_url(), headers=_headers(), timeout=30)


def is_remote() -> bool:
    return bool(os.environ.get("REMOTE_URL", ""))


def ingest_records(records: list[dict], embed_fields: list[str] | None = None) -> dict:
    payload: dict = {"records": records}
    if embed_fields:
        payload["embed_fields"] = embed_fields
    with _client() as c:
        r = c.post("/webhook", json=payload)
        r.raise_for_status()
        return r.json()


def ingest_csv(
    file_path: str,
    record_id_column: str,
    analysis_type: str,
    data_columns: list[str],
    entity_id_column: str | None = None,
    entity_name_column: str | None = None,
    embed_fields: list[str] | None = None,
) -> dict:
    """Parse CSV locally, then send records to the remote API."""
    import csv

    records: list[dict] = []
    with open(file_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if record_id_column not in row:
                msg = f"Column '{record_id_column}' not found"
                return {"ingested": 0, "updated": 0, "errors": [msg]}
            data = {col: row.get(col, "") for col in data_columns if col in row}
            rec = {
                "record_id": row[record_id_column],
                "analysis_type": analysis_type,
                "data": data,
            }
            if entity_id_column and entity_id_column in row:
                rec["entity_id"] = row[entity_id_column]
            if entity_name_column and entity_name_column in row:
                rec["entity_name"] = row[entity_name_column]
            records.append(rec)

    return ingest_records(records, embed_fields=embed_fields)


def query_records(
    analysis_type: str | None = None,
    entity_id: str | None = None,
    tags: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    search_data: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    params: dict = {"limit": limit, "offset": offset}
    if analysis_type:
        params["analysis_type"] = analysis_type
    if entity_id:
        params["entity_id"] = entity_id
    if since:
        params["since"] = since
    if until:
        params["until"] = until
    if search_data:
        params["search_data"] = search_data
    if tags:
        params["tags"] = ",".join(tags)

    with _client() as c:
        r = c.get("/api/v1/records", params=params)
        r.raise_for_status()
        return r.json()


def get_record(record_id: str) -> dict | None:
    with _client() as c:
        r = c.get(f"/api/v1/records/{record_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


def list_analysis_types() -> list[dict]:
    with _client() as c:
        r = c.get("/api/v1/analysis-types")
        r.raise_for_status()
        return r.json()


def get_analytics(
    analysis_type: str | None = None,
    since: str | None = None,
) -> dict:
    params: dict = {}
    if analysis_type:
        params["analysis_type"] = analysis_type
    if since:
        params["since"] = since

    with _client() as c:
        r = c.get("/api/v1/analytics", params=params)
        r.raise_for_status()
        return r.json()


def delete_records(
    analysis_type: str | None = None,
    older_than: str | None = None,
    record_ids: list[str] | None = None,
) -> dict:
    params: dict = {}
    if analysis_type:
        params["analysis_type"] = analysis_type
    if older_than:
        params["older_than"] = older_than
    if record_ids:
        params["record_ids"] = ",".join(record_ids)

    with _client() as c:
        r = c.request("DELETE", "/api/v1/records", params=params)
        r.raise_for_status()
        return r.json()
