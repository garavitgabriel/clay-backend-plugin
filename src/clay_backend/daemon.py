"""Standalone webhook daemon — runs the HTTP server without Claude Code.

Usage:
    clay-webhook-daemon                           # default port 8742
    clay-webhook-daemon --port 9000               # custom port
    clay-webhook-daemon --data-dir ~/clay         # custom data directory
    clay-webhook-daemon --schedules schedules.yaml  # enable scheduled analysis

Run this in the background to collect Clay webhooks 24/7,
even when Claude Code isn't open. Data accumulates in the same
SQLite database the plugin reads from.

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

from .database import init_db
from .webhook_server import DEFAULT_PORT, app

logger = logging.getLogger("clay-webhook-daemon")


def main():
    parser = argparse.ArgumentParser(
        description="Clay Backend Plugin — webhook receiver + scheduled analysis",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("WEBHOOK_PORT", str(DEFAULT_PORT))),
        help=f"Port to listen on (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=os.environ.get("CLAY_DATA_DIR", "."),
        help="Directory for the SQLite database (default: current dir)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--schedules",
        type=str,
        default=os.environ.get("SCHEDULES_PATH", ""),
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

    # Start scheduler if config provided
    scheduler = None
    if args.schedules:
        os.environ["SCHEDULES_PATH"] = args.schedules
        from .scheduler import start_scheduler

        scheduler = start_scheduler(args.schedules)
        if scheduler:
            jobs = scheduler.get_jobs()
            print(f"Scheduler: {len(jobs)} job(s) loaded from {args.schedules}")
            for job in jobs:
                print(f"  - {job.name}: next run at {job.next_run_time}")
        else:
            print(f"Scheduler: no schedules found in {args.schedules}")

    print(f"\nClay webhook daemon starting on http://{args.host}:{args.port}")
    print(f"Data directory: {args.data_dir}")
    print(f"Webhook endpoint: http://{args.host}:{args.port}/webhook")
    print(f"Health check:     http://{args.host}:{args.port}/health")
    print("Press Ctrl+C to stop\n")

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
