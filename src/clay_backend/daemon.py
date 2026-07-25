"""Standalone webhook daemon — runs the HTTP server without Claude Code.

Usage:
    clay-webhook-daemon                           # default port 8742
    clay-webhook-daemon --port 9000               # custom port
    clay-webhook-daemon --data-dir ~/clay         # custom data directory
    clay-webhook-daemon --schedules schedules.yaml  # enable scheduled analysis

Run this in the background to collect Clay webhooks 24/7,
even when Claude Code isn't open. Data accumulates in the same
SQLite database the plugin reads from — both resolve CLAY_DATA_DIR first and
fall back to the same shared default (~/.clay-backend), so the daemon and the
plugin never end up on different clay.db files by accident.

With --schedules, the daemon also runs automated analysis on cron
(e.g., weekly coaching digests to Slack).
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys

import uvicorn

from . import config
from .database import get_db_path, init_db, vec_available, vec_unavailable_reason
from .webhook_server import app, probe_port

logger = logging.getLogger("clay-webhook-daemon")


def main():
    parser = argparse.ArgumentParser(
        description="Clay Backend Plugin — webhook receiver + scheduled analysis",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=config.webhook_port(),
        help=f"Port to listen on (default: {config.DEFAULT_WEBHOOK_PORT})",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=str(config.resolve_data_dir()),
        help=(
            "Directory for the SQLite database "
            f"(default: CLAY_DATA_DIR, else {config.DEFAULT_DATA_DIR})"
        ),
    )
    parser.add_argument(
        "--host",
        type=str,
        default=config.webhook_host(),
        help="Host to bind to (default: 127.0.0.1; use 0.0.0.0 to expose beyond this machine)",
    )
    parser.add_argument(
        "--schedules",
        type=str,
        default=config.schedules_path(),
        help="Path to schedules.yaml for automated analysis (optional)",
    )
    args = parser.parse_args()

    os.environ["CLAY_DATA_DIR"] = args.data_dir
    os.environ["WEBHOOK_PORT"] = str(args.port)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    init_db()
    db_path = get_db_path()

    # Fail fast and legibly on a busy port. uvicorn catches its own bind error
    # and prints a bare errno, which leaves the operator guessing whether the
    # port belongs to another daemon, a Claude Code session, or something else.
    owner, health = probe_port(args.host, args.port)
    if owner != "free":
        who = (
            "another clay-backend webhook receiver"
            if owner == "clay"
            else "a process that is not a clay-backend receiver"
        )
        print(
            f"\nPort {args.host}:{args.port} is already held by {who}.",
            file=sys.stderr,
        )
        if owner == "clay":
            other_db = health.get("db_path", "unknown")
            print(
                f"It writes to {other_db}.\n"
                f"If that is the receiver you want, leave it running — a second one "
                f"is not needed.\n"
                f"Otherwise stop it, or start this one on another port: "
                f"clay-webhook-daemon --port {args.port + 1}",
                file=sys.stderr,
            )
        else:
            print(
                f"Free the port, or start on another one: "
                f"clay-webhook-daemon --port {args.port + 1}\n"
                f"Inspect the current owner with: lsof -i :{args.port}",
                file=sys.stderr,
            )
        sys.exit(1)

    # Start scheduler if config provided
    scheduler = None
    if args.schedules:
        os.environ["SCHEDULES_PATH"] = args.schedules
        from .scheduler import start_scheduler

        scheduler = start_scheduler(args.schedules)
        if scheduler:
            jobs = scheduler.get_jobs()
            print(f"Scheduler: {len(jobs)} job(s) loaded from {args.schedules}", flush=True)
            for job in jobs:
                print(f"  - {job.name}: next run at {job.next_run_time}", flush=True)
        else:
            print(f"Scheduler: no schedules found in {args.schedules}", flush=True)

    print(f"\nClay webhook daemon starting on http://{args.host}:{args.port}", flush=True)
    # The absolute DB path is the single most useful startup line: a daemon and
    # a plugin session on different paths is the "0 records" failure mode.
    print(f"Database:         {db_path}", flush=True)
    print(f"Webhook endpoint: http://{args.host}:{args.port}/webhook", flush=True)
    print(f"Health check:     http://{args.host}:{args.port}/health", flush=True)
    if not vec_available():
        print(f"Semantic search:  disabled — {vec_unavailable_reason()}", flush=True)
    print("Press Ctrl+C to stop\n", flush=True)

    def handle_signal(sig, frame):
        if scheduler:
            scheduler.shutdown(wait=False)
        print("\nShutting down...")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
