"""Lightweight HTTP webhook server that runs alongside the MCP server.

Accepts POST requests from Clay (or any source) and stores records
in the same SQLite database the MCP tools use.
"""

from __future__ import annotations

import hmac
import logging
import threading

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import config
from .models import RecordInput
from .services import record_service

logger = logging.getLogger(__name__)

DEFAULT_PORT = config.DEFAULT_WEBHOOK_PORT
DEFAULT_HOST = config.DEFAULT_WEBHOOK_HOST


def _check_auth(request: Request) -> JSONResponse | None:
    """Verify API key if WEBHOOK_API_KEY is set. Returns error response or None."""
    api_key = config.webhook_api_key()
    if not api_key:
        return None

    # Constant-time comparisons to avoid timing side channels on the secret.
    auth_header = request.headers.get("Authorization", "")
    if hmac.compare_digest(auth_header, f"Bearer {api_key}"):
        return None

    # Also accept X-API-Key header (common in webhook configs)
    if hmac.compare_digest(request.headers.get("X-API-Key", ""), api_key):
        return None

    return JSONResponse({"error": "Unauthorized"}, status_code=401)


async def handle_webhook(request: Request) -> JSONResponse:
    """Accept a Clay webhook payload and store the record."""
    auth_error = _check_auth(request)
    if auth_error:
        return auth_error

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

    payload = result.model_dump()

    # Running totals per analysis_type. This response lands in a Clay cell, so
    # it doubles as a progress meter for the column ("17 of my 20 rows landed").
    payload["total_for_type"] = {
        t: record_service.count_by_type(t)
        for t in sorted({rec.analysis_type for rec in parsed})
    }

    return JSONResponse(payload)


async def health(request: Request) -> JSONResponse:
    """Health check endpoint.

    Reports the resolved database path so an operator (or the MCP server's
    coexistence probe) can tell at a glance whether the daemon and the plugin
    are pointed at the same clay.db.
    """
    from .database import get_db_path

    return JSONResponse(
        {
            "status": "ok",
            "service": "clay-backend-webhook",
            "db_path": get_db_path(),
        }
    )


app = Starlette(
    routes=[
        Route("/webhook", handle_webhook, methods=["POST"]),
        Route("/health", health, methods=["GET"]),
    ],
)


def _connect_host(host: str) -> str:
    """The address to dial when probing a server bound to `host`."""
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host


def probe_port(host: str, port: int, timeout: float = 0.75) -> tuple[str, dict]:
    """Find out who, if anyone, is already listening on (host, port).

    Returns one of:
      ("free",  {})                 — nothing is listening
      ("clay",  {<health payload>}) — a clay-backend webhook receiver answers
      ("other", {})                 — something else holds the port
    """
    import json as _json
    import socket
    import urllib.error
    import urllib.request

    target = _connect_host(host)

    try:
        with socket.create_connection((target, port), timeout=timeout):
            pass
    except OSError:
        return "free", {}

    try:
        with urllib.request.urlopen(
            f"http://{target}:{port}/health", timeout=timeout
        ) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return "other", {}

    if isinstance(payload, dict) and payload.get("service") == "clay-backend-webhook":
        return "clay", payload

    return "other", {}


def ensure_webhook_server(port: int | None = None) -> dict:
    """Start the webhook receiver unless something already owns the port.

    Coexisting with a standalone `clay-webhook-daemon` is a supported topology
    (24/7 ingest + interactive Claude Code sessions), so a busy port is not an
    error — it just means this process should not start a second receiver.
    Silently losing the bind inside a daemon thread is what made this confusing
    in the field, so every outcome is reported.

    Returns {"status": "started" | "external" | "conflict", "port": int, ...}.
    """
    from .database import get_db_path

    port = port or config.webhook_port()
    host = config.webhook_host()

    status, info = probe_port(host, port)

    if status == "clay":
        detail = {
            "status": "external",
            "port": port,
            "host": host,
            "message": (
                f"External clay-backend webhook daemon detected on port {port} — "
                f"not starting a second receiver."
            ),
        }
        remote_db = info.get("db_path")
        if remote_db:
            detail["daemon_db_path"] = remote_db
            local_db = get_db_path()
            if remote_db != local_db:
                detail["warning"] = (
                    f"That daemon writes to {remote_db} but this session reads "
                    f"{local_db}. Records ingested by the daemon will not show up "
                    f"here. Set CLAY_DATA_DIR to the same directory for both."
                )
        logger.warning(detail.get("warning") or detail["message"])
        return detail

    if status == "other":
        detail = {
            "status": "conflict",
            "port": port,
            "host": host,
            "message": (
                f"Port {port} is held by another process that is not a clay-backend "
                f"webhook receiver. The webhook endpoint is unavailable this session. "
                f"Free the port, or set WEBHOOK_PORT to something else."
            ),
        }
        logger.warning(detail["message"])
        return detail

    thread = start_webhook_server(port)
    return {
        "status": "started",
        "port": port,
        "host": host,
        "thread": thread,
        "message": f"Webhook server listening on http://{host}:{port}/webhook",
    }


def start_webhook_server(port: int | None = None) -> threading.Thread:
    """Start the webhook HTTP server in a background daemon thread.

    Returns the thread (already started).
    """
    port = port or config.webhook_port()
    # Loopback by default: ngrok forwards to localhost, so exposing the port to
    # the whole LAN buys nothing and opens unauthenticated ingest when no
    # WEBHOOK_API_KEY is set. Set WEBHOOK_HOST=0.0.0.0 to opt into a wider bind.
    host = config.webhook_host()

    server_config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(server_config)

    thread = threading.Thread(target=server.run, daemon=True, name="webhook-server")
    thread.start()

    logger.info(f"Webhook server started on http://{host}:{port}/webhook")
    return thread
