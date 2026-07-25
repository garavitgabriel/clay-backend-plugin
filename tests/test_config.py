"""Environment-config tolerance and data-directory unification.

These cover the two first-run failures from the 2026-07-24 field test:

  1. `.mcp.json` injects unset user_config values as "" or as the unresolved
     "${user_config.NAME}" literal. `int("")` used to kill the MCP server
     before it could report anything, so /mcp just showed "failed".
  2. The daemon defaulted its data dir to CWD while the plugin used
     CLAY_DATA_DIR, so the two halves silently wrote to different clay.db
     files and the user saw "0 records".
"""

from __future__ import annotations

import pytest

from clay_backend import config

# --------------------------------------------------------------------------
# env_int / env_str / is_unset
# --------------------------------------------------------------------------


UNSET_SHAPES = [
    None,  # variable absent entirely
    "",  # plugin loader injected an empty string
    "   ",  # whitespace-only
    "${user_config.WEBHOOK_PORT}",  # placeholder never resolved
    "  ${user_config.WEBHOOK_PORT}  ",
]


@pytest.mark.parametrize("raw", UNSET_SHAPES)
def test_env_int_treats_unset_shapes_as_default(raw, monkeypatch):
    if raw is None:
        monkeypatch.delenv("WEBHOOK_PORT", raising=False)
    else:
        monkeypatch.setenv("WEBHOOK_PORT", raw)
    assert config.env_int("WEBHOOK_PORT", 8742) == 8742


@pytest.mark.parametrize("raw", UNSET_SHAPES)
def test_env_str_treats_unset_shapes_as_default(raw, monkeypatch):
    if raw is None:
        monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("EMBEDDING_PROVIDER", raw)
    assert config.env_str("EMBEDDING_PROVIDER", "local") == "local"


@pytest.mark.parametrize("raw", UNSET_SHAPES)
def test_is_unset(raw):
    assert config.is_unset(raw) is True


def test_env_int_reads_a_real_value(monkeypatch):
    monkeypatch.setenv("WEBHOOK_PORT", " 9001 ")
    assert config.env_int("WEBHOOK_PORT", 8742) == 9001


def test_env_int_falls_back_on_garbage_instead_of_raising(monkeypatch):
    monkeypatch.setenv("WEBHOOK_PORT", "not-a-port")
    assert config.env_int("WEBHOOK_PORT", 8742) == 8742


def test_env_str_strips_and_reads_a_real_value(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "  openai  ")
    assert config.env_str("EMBEDDING_PROVIDER", "local") == "openai"


def test_webhook_helpers_survive_a_fully_unconfigured_plugin(monkeypatch):
    """The exact `--plugin-dir .` with zero user_config state."""
    for name in (
        "WEBHOOK_PORT",
        "WEBHOOK_HOST",
        "WEBHOOK_API_KEY",
        "EMBEDDING_PROVIDER",
        "OPENAI_API_KEY",
        "REMOTE_URL",
        "REMOTE_API_KEY",
    ):
        monkeypatch.setenv(name, f"${{user_config.{name}}}")

    assert config.webhook_port() == config.DEFAULT_WEBHOOK_PORT
    assert config.webhook_host() == config.DEFAULT_WEBHOOK_HOST
    assert config.webhook_api_key() == ""
    assert config.embedding_provider_name() == ""
    assert config.openai_api_key() == ""
    assert config.remote_url() == ""
    assert config.remote_api_key() == ""


def test_remote_mode_off_when_remote_url_is_a_placeholder(monkeypatch):
    """An unresolved REMOTE_URL must not flip the plugin into remote mode."""
    from clay_backend.services import remote_service

    monkeypatch.setenv("REMOTE_URL", "${user_config.REMOTE_URL}")
    assert remote_service.is_remote() is False

    monkeypatch.setenv("REMOTE_URL", "")
    assert remote_service.is_remote() is False

    monkeypatch.setenv("REMOTE_URL", "https://example.railway.app/")
    assert remote_service.is_remote() is True
    assert config.remote_url() == "https://example.railway.app"


# --------------------------------------------------------------------------
# Shared data-directory resolution
# --------------------------------------------------------------------------


def test_resolve_data_dir_uses_shared_default_when_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAY_DATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config.resolve_data_dir() == (tmp_path / ".clay-backend").resolve()


def test_resolve_data_dir_ignores_placeholder(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAY_DATA_DIR", "${CLAUDE_PLUGIN_DATA}")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config.resolve_data_dir() == (tmp_path / ".clay-backend").resolve()


def test_resolve_data_dir_is_absolute(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAY_DATA_DIR", str(tmp_path))
    resolved = config.resolve_data_dir()
    assert resolved.is_absolute()
    assert resolved == tmp_path.resolve()


def test_daemon_and_mcp_resolve_the_same_db_path(monkeypatch, tmp_path):
    """The split-brain fix: both halves must land on one clay.db.

    The daemon's --data-dir default and the MCP server's database path now come
    from the same resolver, so a shared CLAY_DATA_DIR (or no config at all)
    puts them on the same file.
    """
    import clay_backend.database as database

    shared = tmp_path / "shared-clay"
    monkeypatch.setenv("CLAY_DATA_DIR", str(shared))

    # What the daemon would use as its --data-dir default.
    daemon_default = str(config.resolve_data_dir())

    # What the MCP server actually opens.
    monkeypatch.setattr(database, "_DB_PATH", None)
    mcp_db_path = database.get_db_path()

    # And what the daemon opens after applying its own default.
    monkeypatch.setenv("CLAY_DATA_DIR", daemon_default)
    monkeypatch.setattr(database, "_DB_PATH", None)
    daemon_db_path = database.get_db_path()

    assert daemon_db_path == mcp_db_path
    assert daemon_db_path == str(shared.resolve() / "clay.db")

    database._DB_PATH = None


def test_unconfigured_daemon_and_mcp_agree_on_the_default(monkeypatch, tmp_path):
    """With nothing set at all, both still meet at ~/.clay-backend/clay.db."""
    import clay_backend.database as database

    monkeypatch.delenv("CLAY_DATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # keep the real home dir untouched
    monkeypatch.setattr(database, "_DB_PATH", None)

    expected = str((tmp_path / ".clay-backend").resolve() / "clay.db")
    assert database.get_db_path() == expected
    assert config.DEFAULT_DATA_DIR == "~/.clay-backend"

    database._DB_PATH = None
