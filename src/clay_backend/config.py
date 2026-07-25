"""Tolerant environment configuration.

Every user-facing setting arrives through an environment variable, and the
plugin loader is not careful about what it injects. When a `userConfig` entry
in `.claude-plugin/plugin.json` has no value (the normal state on a clean
`--plugin-dir .` launch), `.mcp.json` injects either an empty string or the
unresolved `${user_config.NAME}` literal. Naive parsing turns that into
`int("")` -> ValueError and the MCP server dies with a bare traceback and a
`✘ failed` badge in `/mcp`.

Everything here treats those three shapes — missing, empty, unresolved
placeholder — as "the user did not set this", and falls back to the default.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Shared default data directory. Both the MCP server and the standalone daemon
# resolve to this when CLAY_DATA_DIR is unset, so the two halves of the plugin
# always agree on which clay.db they are talking about regardless of CWD.
DEFAULT_DATA_DIR = "~/.clay-backend"

DEFAULT_WEBHOOK_PORT = 8742
DEFAULT_WEBHOOK_HOST = "127.0.0.1"


def is_unset(raw: str | None) -> bool:
    """True when an env value means "not configured".

    Covers the three ways an unset user_config reaches us: absent, empty
    string, or an unresolved `${user_config.NAME}` placeholder.
    """
    if raw is None:
        return True
    stripped = raw.strip()
    return not stripped or stripped.startswith("${")


def env_str(name: str, default: str = "") -> str:
    """Read a string env var, treating unset/empty/placeholder as the default."""
    raw = os.environ.get(name)
    return default if is_unset(raw) else raw.strip()


def env_int(name: str, default: int) -> int:
    """Read an int env var, treating unset/empty/placeholder as the default.

    A present-but-unparseable value is a real misconfiguration, so it is logged
    — but it still falls back to the default rather than killing the process.
    """
    raw = os.environ.get(name)
    if is_unset(raw):
        return default
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning("%s=%r is not an integer — falling back to %d", name, raw, default)
        return default


def env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean env var, treating unset/empty/placeholder as the default."""
    raw = env_str(name)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def resolve_data_dir() -> Path:
    """The directory holding clay.db, as an absolute path.

    Resolution order — identical for the MCP server, the webhook daemon, and
    the doctor CLI:
      1. CLAY_DATA_DIR
      2. DEFAULT_DATA_DIR (~/.clay-backend)
    """
    return Path(env_str("CLAY_DATA_DIR", DEFAULT_DATA_DIR)).expanduser().resolve()


def webhook_port() -> int:
    return env_int("WEBHOOK_PORT", DEFAULT_WEBHOOK_PORT)


def webhook_host() -> str:
    return env_str("WEBHOOK_HOST", DEFAULT_WEBHOOK_HOST)


def webhook_api_key() -> str:
    return env_str("WEBHOOK_API_KEY")


def remote_url() -> str:
    return env_str("REMOTE_URL").rstrip("/")


def remote_api_key() -> str:
    return env_str("REMOTE_API_KEY")


def embedding_provider_name() -> str:
    return env_str("EMBEDDING_PROVIDER").lower()


def openai_api_key() -> str:
    return env_str("OPENAI_API_KEY")


def schedules_path() -> str:
    return env_str("SCHEDULES_PATH")
