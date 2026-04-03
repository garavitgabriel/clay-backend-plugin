"""MCP server for the Clay Backend Plugin."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .database import init_db
from .models import RecordInput
from .services import record_service, search_service

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
        "Deduplicates on record_id + analysis_type — sending the same record "
        "again updates it instead of creating a duplicate."
    ),
)
def ingest_records(
    records: list[dict],
    embed_fields: list[str] | None = None,
) -> dict:
    """Import records from Clay."""
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
        "analysis_type='call_analysis', data_columns=['AI Summary', 'Score', 'Rep Name']"
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
) -> dict:
    """Import records from a CSV file."""
    try:
        result = record_service.ingest_csv(
            file_path=file_path,
            record_id_column=record_id_column,
            analysis_type=analysis_type,
            data_columns=data_columns,
            entity_id_column=entity_id_column,
            entity_name_column=entity_name_column,
            embed_fields=embed_fields,
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
            "error": "At least one filter required (analysis_type, older_than, or record_ids)",
        }
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
        "Requires embeddings to be enabled (EMBEDDING_PROVIDER set). "
        "Returns records ranked by similarity score."
    ),
)
def semantic_search(
    query: str,
    analysis_type: str | None = None,
    top_k: int = 10,
) -> list[dict]:
    """Semantic search across records."""
    return search_service.semantic_search(
        query=query,
        analysis_type=analysis_type,
        top_k=top_k,
    )


@mcp.tool(
    name="get_webhook_url",
    description=(
        "Get the URL of the local webhook server. "
        "Use this to tell the user what URL to configure in Clay's HTTP API enrichment. "
        "The webhook server accepts POST requests with Clay record data."
    ),
)
def get_webhook_url() -> dict:
    """Get the webhook server URL."""
    import os

    port = int(os.environ.get("WEBHOOK_PORT", "8742"))
    api_key = os.environ.get("WEBHOOK_API_KEY", "")

    info: dict = {
        "webhook_url": f"http://localhost:{port}/webhook",
        "health_url": f"http://localhost:{port}/health",
        "method": "POST",
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
            "clay_setup": (
                "In Clay's HTTP API column, add a header: "
                f"Authorization = Bearer {api_key}"
            ),
        }
    else:
        info["authentication"] = "None (set WEBHOOK_API_KEY to require auth)"

    info["external_access"] = (
        "For Clay cloud → local machine, run: ngrok http "
        f"{port} — then use the ngrok URL in Clay."
    )

    return info


def main():
    """Entry point for the MCP server."""
    init_db()

    # Start webhook HTTP server in background
    from .webhook_server import start_webhook_server

    start_webhook_server()

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
