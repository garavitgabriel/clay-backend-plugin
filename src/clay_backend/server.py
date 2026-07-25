"""MCP server for the Clay Backend Plugin."""

from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from . import config
from .database import get_db_path, init_db, vec_available, vec_unavailable_reason
from .models import RecordInput
from .services import record_service, remote_service, search_service

logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="clay-backend",
    instructions=(
        "Clay Backend Plugin — stores, searches, and helps analyze patterns across Clay data. "
        "Use these tools to import Clay exports (CSV or JSON), query stored records, "
        "and find insights across analysis results. "
        "When the user asks about patterns or trends, use query_records to fetch data "
        "and then synthesize the results yourself."
    ),
)


@mcp.tool(
    name="ingest_records",
    description=(
        "Import analysis records from Clay. Accepts a list of records as JSON. "
        "Each record needs a record_id (unique ID from Clay), analysis_type "
        "(e.g. 'call_analysis'), and data (the analysis payload). "
        "Optionally set created_at (ISO 8601) to the real event time when "
        "back-filling historical data — time-window queries filter on it. "
        "Deduplicates on record_id + analysis_type — sending the same record "
        "again updates it instead of creating a duplicate."
    ),
)
def ingest_records(
    records: list[dict],
    embed_fields: list[str] | None = None,
) -> dict:
    """Import records from Clay."""
    if remote_service.is_remote():
        return remote_service.ingest_records(records, embed_fields)

    parsed = []
    errors = []
    for i, rec in enumerate(records):
        try:
            parsed.append(RecordInput(**rec))
        except Exception as e:
            errors.append(f"Record {i}: {e}")

    if not parsed and errors:
        return {"ingested": 0, "updated": 0, "errors": errors}

    result = record_service.ingest_records(parsed, embed_fields=embed_fields)
    result.errors.extend(errors)
    return result.model_dump()


@mcp.tool(
    name="ingest_csv",
    description=(
        "Import records from a CSV file (typically a Clay table export). "
        "Provide the file path, which column contains the unique row ID, "
        "a name for the analysis type, and which columns to include as data. "
        "Example: file_path='/tmp/clay_export.csv', record_id_column='Row ID', "
        "analysis_type='call_analysis', data_columns=['AI Summary', 'Score', 'Rep Name']. "
        "Optionally set created_at_column to the CSV column holding the real event "
        "date (e.g. call date) so time-window queries work on back-filled exports "
        "(local mode only)."
    ),
)
def ingest_csv(
    file_path: str,
    record_id_column: str,
    analysis_type: str,
    data_columns: list[str],
    entity_id_column: str | None = None,
    entity_name_column: str | None = None,
    embed_fields: list[str] | None = None,
    created_at_column: str | None = None,
) -> dict:
    """Import records from a CSV file."""
    try:
        if remote_service.is_remote():
            return remote_service.ingest_csv(
                file_path=file_path,
                record_id_column=record_id_column,
                analysis_type=analysis_type,
                data_columns=data_columns,
                entity_id_column=entity_id_column,
                entity_name_column=entity_name_column,
                embed_fields=embed_fields,
            )
        result = record_service.ingest_csv(
            file_path=file_path,
            record_id_column=record_id_column,
            analysis_type=analysis_type,
            data_columns=data_columns,
            entity_id_column=entity_id_column,
            entity_name_column=entity_name_column,
            embed_fields=embed_fields,
            created_at_column=created_at_column,
        )
        return result.model_dump()
    except FileNotFoundError:
        return {"ingested": 0, "updated": 0, "errors": [f"File not found: {file_path}"]}
    except Exception as e:
        return {"ingested": 0, "updated": 0, "errors": [str(e)]}


@mcp.tool(
    name="query_records",
    description=(
        "Fetch stored analysis records with optional filters. "
        "Filter by analysis_type, entity_id, tags, date range (since/until as ISO dates), "
        "or text search within the data JSON. "
        "Returns up to 200 records, ordered by most recent first."
    ),
)
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
    """Query records with filters."""
    if remote_service.is_remote():
        return remote_service.query_records(
            analysis_type=analysis_type,
            entity_id=entity_id,
            tags=tags,
            since=since,
            until=until,
            search_data=search_data,
            limit=limit,
            offset=offset,
        )
    records = record_service.query_records(
        analysis_type=analysis_type,
        entity_id=entity_id,
        tags=tags,
        since=since,
        until=until,
        search_data=search_data,
        limit=limit,
        offset=offset,
    )
    return [r.model_dump() for r in records]


@mcp.tool(
    name="get_record",
    description="Get a single record by its UUID. Returns the full record with all data fields.",
)
def get_record(id: str) -> dict | str:
    """Get a record by UUID."""
    if remote_service.is_remote():
        record = remote_service.get_record(id)
        if record is None:
            return f"Record not found: {id}"
        return record
    record = record_service.get_record(id)
    if record is None:
        return f"Record not found: {id}"
    return record.model_dump()


@mcp.tool(
    name="list_analysis_types",
    description=(
        "List all analysis types currently stored, with counts and date ranges. "
        "Use this to discover what data is available before querying."
    ),
)
def list_analysis_types() -> list[dict]:
    """List stored analysis types."""
    if remote_service.is_remote():
        types = remote_service.list_analysis_types()
        if not types:
            msg = "No records stored yet. Import data using ingest_records or ingest_csv."
            return [{"message": msg}]
        return types
    types = record_service.list_analysis_types()
    if not types:
        msg = "No records stored yet. Import data using ingest_records or ingest_csv."
        return [{"message": msg}]
    return [t.model_dump() for t in types]


@mcp.tool(
    name="get_analytics",
    description=(
        "Get summary statistics: total records, breakdown by type, "
        "top entities, and storage size. "
        "Optionally filter by analysis_type and/or date range."
    ),
)
def get_analytics(
    analysis_type: str | None = None,
    since: str | None = None,
) -> dict:
    """Get analytics summary."""
    if remote_service.is_remote():
        return remote_service.get_analytics(
            analysis_type=analysis_type,
            since=since,
        )
    analytics = record_service.get_analytics(
        analysis_type=analysis_type,
        since=since,
    )
    return analytics.model_dump()


@mcp.tool(
    name="delete_records",
    description=(
        "Delete stored records. Filter by analysis_type, older_than (ISO date), "
        "or specific record UUIDs. At least one filter is required to prevent "
        "accidental deletion of all data."
    ),
)
def delete_records(
    analysis_type: str | None = None,
    older_than: str | None = None,
    record_ids: list[str] | None = None,
) -> dict:
    """Delete records matching criteria."""
    if not analysis_type and not older_than and not record_ids:
        return {
            "deleted": 0,
            "error": "At least one filter required",
        }
    if remote_service.is_remote():
        return remote_service.delete_records(
            analysis_type=analysis_type,
            older_than=older_than,
            record_ids=record_ids,
        )
    result = record_service.delete_records(
        analysis_type=analysis_type,
        older_than=older_than,
        record_ids=record_ids,
    )
    return result.model_dump()


@mcp.tool(
    name="semantic_search",
    description=(
        "Search records using natural language. Uses vector embeddings to find "
        "semantically similar records — not just keyword matches. "
        "Example: 'calls where budget was not discussed' will find records about "
        "missing budget conversations even if those exact words aren't used. "
        "Requires embeddings to be enabled (EMBEDDING_PROVIDER set) in local mode. "
        "Returns records ranked by similarity score."
    ),
)
def semantic_search(
    query: str,
    analysis_type: str | None = None,
    top_k: int = 10,
) -> list[dict]:
    """Semantic search across records."""
    if remote_service.is_remote():
        # Remote mode: fall back to text search (hosted service doesn't have embeddings yet)
        records = remote_service.query_records(
            analysis_type=analysis_type,
            search_data=query,
            limit=top_k,
        )
        return [
            {"record": r, "similarity_score": None, "note": "text search (remote mode)"}
            for r in records
        ]
    return search_service.semantic_search(
        query=query,
        analysis_type=analysis_type,
        top_k=top_k,
    )


@mcp.tool(
    name="get_webhook_url",
    description=(
        "Get the webhook URL for Clay configuration. "
        "Shows the URL, body format, and authentication details. "
        "In remote mode, returns the hosted service URL."
    ),
)
def get_webhook_url() -> dict:
    """Get the webhook server URL."""
    if remote_service.is_remote():
        remote_url = config.remote_url()
        api_key = config.remote_api_key()
        info: dict = {
            "webhook_url": f"{remote_url}/webhook",
            "health_url": f"{remote_url}/health",
            "method": "POST",
            "mode": "remote (hosted service)",
            "body_format": {
                "record_id": "{{Row ID}}",
                "analysis_type": "your_analysis_type",
                "data": {"field1": "{{Column 1}}", "field2": "{{Column 2}}"},
                "entity_id": "{{Deal ID}}  (optional)",
                "entity_name": "{{Company Name}}  (optional)",
            },
        }
        if api_key:
            info["authentication"] = {
                "header": f"Authorization: Bearer {api_key}",
                "clay_setup": f"In Clay, add header: Authorization = Bearer {api_key}",
            }
        return info

    port = config.webhook_port()
    api_key = config.webhook_api_key()

    info = {
        "webhook_url": f"http://localhost:{port}/webhook",
        "health_url": f"http://localhost:{port}/health",
        "method": "POST",
        "mode": "local",
        "body_format": {
            "record_id": "{{Row ID}}",
            "analysis_type": "your_analysis_type",
            "data": {"field1": "{{Column 1}}", "field2": "{{Column 2}}"},
            "entity_id": "{{Deal ID}}  (optional)",
            "entity_name": "{{Company Name}}  (optional)",
        },
    }

    if api_key:
        info["authentication"] = {
            "type": "Bearer token or X-API-Key header",
            "header": f"Authorization: Bearer {api_key}",
            "clay_setup": f"In Clay, add header: Authorization = Bearer {api_key}",
        }
    else:
        info["authentication"] = "None (set WEBHOOK_API_KEY to require auth)"

    info["external_access"] = (
        f"For Clay cloud, expose port {port} with a tunnel: "
        f"`cloudflared tunnel --url http://127.0.0.1:{port}` (no account needed) "
        f"or `ngrok http {port}`. Then use the tunnel URL + /webhook in Clay."
    )
    info["verify"] = (
        "Before wiring Clay, confirm the tunnel reaches you: "
        "curl https://<tunnel-host>/health"
    )

    return info


@mcp.tool(
    name="doctor",
    description=(
        "Run first-run diagnostics on the plugin's environment. Checks SQLite "
        "extension support, the resolved database path and record counts, webhook "
        "port availability (including whether a standalone daemon owns it), the "
        "embedding provider, and webhook authentication. Use this whenever the "
        "plugin reports zero records, semantic search fails, or setup looks wrong."
    ),
)
def doctor() -> dict:
    """Run environment diagnostics."""
    from .doctor import run_checks

    return run_checks()


def main():
    """Entry point for the MCP server."""
    # stdout is the MCP transport — every human-readable line must go to stderr.
    # WARNING keeps the stream to things the user can act on (port conflicts,
    # split-brain data dirs, degraded vector search) rather than per-request noise.
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if not remote_service.is_remote():
        init_db()
        print(f"clay-backend: database at {get_db_path()}", file=sys.stderr)
        if not vec_available():
            print(
                f"clay-backend: semantic search disabled — {vec_unavailable_reason()}",
                file=sys.stderr,
            )

        # Start the webhook HTTP server in the background (local mode only),
        # unless a standalone daemon already owns the port.
        from .webhook_server import ensure_webhook_server

        status = ensure_webhook_server()
        print(f"clay-backend: {status['message']}", file=sys.stderr)
        if status.get("warning"):
            print(f"clay-backend: {status['warning']}", file=sys.stderr)
    else:
        print(f"clay-backend: remote mode — {config.remote_url()}", file=sys.stderr)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
