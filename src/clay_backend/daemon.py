"""Standalone webhook daemon — runs the HTTP server without Claude Code.

Usage:
    clay-webhook-daemon                    # default port 8742
    clay-webhook-daemon --port 9000        # custom port
    clay-webhook-daemon --data-dir ~/clay  # custom data directory

Run this in the background to collect Clay webhooks 24/7,
even when Claude Code isn't open. Data accumulates in the same
SQLite database the plugin reads from.
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
        description="Clay Backend Plugin — standalone webhook receiver",
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
    args = parser.parse_args()

    os.environ["CLAY_DATA_DIR"] = args.data_dir
    os.environ["WEBHOOK_PORT"] = str(args.port)

    init_db()

    print(f"Clay webhook daemon starting on http://{args.host}:{args.port}")
    print(f"Data directory: {args.data_dir}")
    print(f"Webhook endpoint: http://{args.host}:{args.port}/webhook")
    print(f"Health check:     http://{args.host}:{args.port}/health")
    print("Press Ctrl+C to stop\n")

    def handle_signal(sig, frame):
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
