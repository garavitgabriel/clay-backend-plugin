"""Lightweight HTTP webhook server that runs alongside the MCP server.

Accepts POST requests from Clay (or any source) and stores records
in the same SQLite database the MCP tools use.
"""

from __future__ import annotations

import logging
import os
import threading

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .models import RecordInput
from .services import record_service

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8742


async def handle_webhook(request: Request) -> JSONResponse:
    """Accept a Clay webhook payload and store the record."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    # Support both single record and batch
    if isinstance(body, list):
        raw_records = body
    elif isinstance(body, dict):
        # If it has a "records" key, treat as batch
        if "records" in body:
            raw_records = body["records"]
        else:
            raw_records = [body]
    else:
        return JSONResponse({"error": "Expected JSON object or array"}, status_code=400)

    embed_fields = None
    if isinstance(body, dict) and "embed_fields" in body:
        embed_fields = body["embed_fields"]

    parsed = []
    errors = []
    for i, rec in enumerate(raw_records):
        try:
            parsed.append(RecordInput(**rec))
        except Exception as e:
            errors.append(f"Record {i}: {e}")

    if not parsed and errors:
        return JSONResponse(
            {"ingested": 0, "updated": 0, "errors": errors},
            status_code=400,
        )

    result = record_service.ingest_records(parsed, embed_fields=embed_fields)
    result.errors.extend(errors)

    return JSONResponse(result.model_dump())


async def health(request: Request) -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse({"status": "ok", "service": "clay-backend-webhook"})


app = Starlette(
    routes=[
        Route("/webhook", handle_webhook, methods=["POST"]),
        Route("/health", health, methods=["GET"]),
    ],
)


def start_webhook_server(port: int | None = None) -> threading.Thread:
    """Start the webhook HTTP server in a background daemon thread.

    Returns the thread (already started).
    """
    port = port or int(os.environ.get("WEBHOOK_PORT", str(DEFAULT_PORT)))

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True, name="webhook-server")
    thread.start()

    logger.info(f"Webhook server started on http://0.0.0.0:{port}/webhook")
    return thread
