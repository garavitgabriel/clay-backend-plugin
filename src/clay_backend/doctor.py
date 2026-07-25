"""First-run diagnostics.

Every bug the plugin hit in its first field test was environment or config, not
logic: a pyenv Python without SQLite extension support, an empty `WEBHOOK_PORT`
injected by the plugin loader, a daemon and a plugin session pointed at
different `clay.db` files, and a port already held by another receiver. Each of
those is a ten-second check — this module runs all of them and prints the fix.

Usage:
    clay-backend-doctor          # CLI
    (or ask Claude to run the `doctor` MCP tool)
"""

from __future__ import annotations

import os
import sqlite3
import sys

from . import config

PASS = "pass"
WARN = "warn"
FAIL = "fail"


def _check(name: str, status: str, detail: str, fix: str = "") -> dict:
    result = {"check": name, "status": status, "detail": detail}
    if fix:
        result["fix"] = fix
    return result


def check_mode() -> dict:
    """Local SQLite vs hosted remote service."""
    url = config.remote_url()
    if url:
        key = "set" if config.remote_api_key() else "not set"
        return _check(
            "mode",
            PASS,
            f"remote — REMOTE_URL={url} (REMOTE_API_KEY {key})",
        )
    return _check("mode", PASS, "local — records stored in SQLite on this machine")


def check_sqlite_extensions() -> dict:
    """Can this Python load SQLite extensions (required for semantic search)?"""
    db = sqlite3.connect(":memory:")
    try:
        try:
            db.enable_load_extension(True)
        except (AttributeError, sqlite3.OperationalError) as e:
            return _check(
                "sqlite extension support",
                WARN,
                f"this Python was built without SQLite extension support "
                f"(common with pyenv and Apple's /usr/bin/python3): {e}",
                "Semantic search is disabled; ingest, filters, and text search still "
                "work. To enable it, recreate the venv with a uv-managed Python "
                "(`uv venv --python 3.12 --managed-python`) or a python.org build.",
            )
    finally:
        db.close()

    from .database import vec_available, vec_unavailable_reason

    if not vec_available():
        # enable_load_extension exists but sqlite-vec still would not load —
        # a broken/missing wheel rather than a stripped interpreter.
        return _check(
            "sqlite extension support",
            WARN,
            vec_unavailable_reason(),
            "Semantic search is disabled; ingest, filters, and text search still "
            "work. Reinstall sqlite-vec, or recreate the venv with a uv-managed "
            "Python (`uv venv --python 3.12 --managed-python`).",
        )

    return _check(
        "sqlite extension support",
        PASS,
        f"OK — sqlite {sqlite3.sqlite_version}, sqlite-vec loads",
    )


def check_database() -> dict:
    """Resolved DB path, writability, and what is actually in it."""
    from .database import get_db_path, init_db

    if config.remote_url():
        return _check("database", PASS, "skipped — running in remote mode")

    source = "CLAY_DATA_DIR" if not config.is_unset(os.environ.get("CLAY_DATA_DIR")) else "default"

    try:
        init_db()
        path = get_db_path()
    except Exception as e:
        return _check(
            "database",
            FAIL,
            f"could not open the database: {e}",
            "Check that the data directory exists and is writable, "
            "or point CLAY_DATA_DIR somewhere you can write.",
        )

    from .services import record_service

    try:
        types = record_service.list_analysis_types()
    except Exception as e:
        return _check("database", FAIL, f"opened but unreadable: {e}")

    total = sum(t.count for t in types)
    breakdown = ", ".join(f"{t.analysis_type}={t.count}" for t in types) or "no records yet"

    status = PASS if total else WARN
    fix = ""
    if not total:
        fix = (
            "No records stored. If you expected some, confirm the daemon writes here "
            f"too — it prints its database path at startup, and it must match {path}."
        )

    return _check(
        "database",
        status,
        f"{path} (from {source}) — {total} record(s): {breakdown}",
        fix,
    )


def check_webhook_port() -> dict:
    """Is the webhook port free, ours, or somebody else's?"""
    if config.remote_url():
        return _check("webhook port", PASS, "skipped — running in remote mode")

    from .database import get_db_path
    from .webhook_server import probe_port

    host = config.webhook_host()
    port = config.webhook_port()
    status, info = probe_port(host, port)

    if status == "free":
        return _check("webhook port", PASS, f"{host}:{port} is free — the plugin can bind it")

    if status == "clay":
        daemon_db = info.get("db_path")
        local_db = get_db_path()
        if daemon_db and daemon_db != local_db:
            return _check(
                "webhook port",
                WARN,
                f"a clay-backend daemon owns {host}:{port} but writes to {daemon_db}, "
                f"while this process reads {local_db}",
                "Split-brain database. Start the daemon with the same directory: "
                f"clay-webhook-daemon --data-dir {os.path.dirname(local_db)}",
            )
        return _check(
            "webhook port",
            PASS,
            f"a clay-backend daemon already owns {host}:{port} and shares this database",
        )

    return _check(
        "webhook port",
        WARN,
        f"{host}:{port} is held by a process that is not a clay-backend receiver",
        "Free the port, or set WEBHOOK_PORT (plugin config) to an unused one.",
    )


def check_embeddings() -> dict:
    """Embedding provider configuration and whether it actually loads."""
    name = config.embedding_provider_name()
    has_openai_key = bool(config.openai_api_key())

    if not name and not has_openai_key:
        return _check(
            "embeddings",
            PASS,
            "disabled — filters and text search still work",
            "Optional below roughly 1,000 records. To enable: set EMBEDDING_PROVIDER "
            "to 'local' (free, ~80MB model) or 'openai' (needs OPENAI_API_KEY).",
        )

    if name == "openai" and not has_openai_key:
        return _check(
            "embeddings",
            FAIL,
            "EMBEDDING_PROVIDER=openai but OPENAI_API_KEY is not set",
            "Set OPENAI_API_KEY in the plugin config, or switch "
            "EMBEDDING_PROVIDER to 'local'.",
        )

    from .services import embedding_service

    try:
        provider = embedding_service.get_provider()
    except Exception as e:
        return _check("embeddings", FAIL, f"provider failed to initialize: {e}")

    if provider is None:
        return _check(
            "embeddings",
            WARN,
            f"EMBEDDING_PROVIDER={name or '(auto)'} but no provider could be loaded",
            "Install the extra you need: "
            'uv pip install -e ".[local-embeddings]" or ".[openai]".',
        )

    from .database import get_vec_dimension, vec_available

    if not vec_available():
        return _check(
            "embeddings",
            WARN,
            f"{provider.model_name} is configured but vector storage is unavailable",
            "See the 'sqlite extension support' check above. Ingest still works; "
            "semantic_search does not.",
        )

    stored_dim = get_vec_dimension()
    if stored_dim is not None and stored_dim != provider.dimension:
        return _check(
            "embeddings",
            FAIL,
            f"stored vectors are {stored_dim}-dim but {provider.model_name} "
            f"produces {provider.dimension}-dim",
            "Switching providers requires re-embedding. Run delete_records to clear "
            "the store and re-ingest.",
        )

    return _check(
        "embeddings",
        PASS,
        f"{provider.model_name} ({provider.dimension}-dim)",
    )


def check_webhook_auth() -> dict:
    """Whether the webhook endpoint requires a key."""
    if config.remote_url():
        key = "set" if config.remote_api_key() else "not set"
        return _check("webhook auth", PASS, f"remote mode — REMOTE_API_KEY {key}")

    if config.webhook_api_key():
        return _check(
            "webhook auth",
            PASS,
            "WEBHOOK_API_KEY is set — /webhook requires Authorization: Bearer <key>",
        )

    host = config.webhook_host()
    exposed = host not in ("127.0.0.1", "localhost", "::1")
    return _check(
        "webhook auth",
        WARN if exposed else PASS,
        f"WEBHOOK_API_KEY is not set — /webhook accepts unauthenticated posts "
        f"(bound to {host})",
        "Set WEBHOOK_API_KEY before exposing the port through a tunnel (ngrok, "
        "cloudflared) or binding to 0.0.0.0.",
    )


CHECKS = (
    check_mode,
    check_sqlite_extensions,
    check_database,
    check_webhook_port,
    check_embeddings,
    check_webhook_auth,
)


def run_checks() -> dict:
    """Run every diagnostic and return a structured report."""
    results = []
    for fn in CHECKS:
        try:
            results.append(fn())
        except Exception as e:  # a broken check must not hide the others
            results.append(_check(fn.__name__, FAIL, f"check crashed: {e}"))

    counts = {PASS: 0, WARN: 0, FAIL: 0}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    if counts[FAIL]:
        overall = FAIL
    elif counts[WARN]:
        overall = WARN
    else:
        overall = PASS

    return {"overall": overall, "summary": counts, "checks": results}


_SYMBOL = {PASS: "PASS", WARN: "WARN", FAIL: "FAIL"}


def format_report(report: dict) -> str:
    lines = ["", "clay-backend doctor", "=" * 60]
    for r in report["checks"]:
        lines.append(f"[{_SYMBOL[r['status']]}] {r['check']}")
        lines.append(f"       {r['detail']}")
        if r.get("fix"):
            lines.append(f"       fix: {r['fix']}")
    counts = report["summary"]
    lines.append("=" * 60)
    lines.append(
        f"{counts[PASS]} passed, {counts[WARN]} warning(s), {counts[FAIL]} failure(s)"
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    """CLI entry point: `clay-backend-doctor`."""
    report = run_checks()
    print(format_report(report))
    return 1 if report["overall"] == FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
