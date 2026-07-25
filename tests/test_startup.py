"""Startup behaviour: webhook port coexistence and degraded no-vec mode.

Field-test bugs #1 and #4:

  * A pyenv-built CPython has no `Connection.enable_load_extension`, so loading
    sqlite-vec raised AttributeError and the daemon died on a raw traceback.
    Embeddings are optional — the plugin must degrade, not crash.
  * The MCP server always started its own webhook thread. When a standalone
    `clay-webhook-daemon` already held the port, the uvicorn bind failed inside
    a daemon thread with no visible signal. Coexistence is a supported topology
    and must be reported either way.
"""

from __future__ import annotations

import socket
import sqlite3
import threading
import time

import pytest
import uvicorn
from conftest import requires_vec

from clay_backend import config, database
from clay_backend.webhook_server import app, ensure_webhook_server, probe_port


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def live_daemon(clay_db):
    """A real clay-backend webhook receiver on its own port, like the daemon."""
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 10
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    assert server.started, "test webhook daemon never came up"

    yield port

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def foreign_server():
    """A plain TCP listener that is emphatically not a clay-backend receiver."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(5)
    port = sock.getsockname()[1]

    stop = threading.Event()

    def serve():
        sock.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except (socket.timeout, OSError):
                continue
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield port
    stop.set()
    thread.join(timeout=2)
    sock.close()


# --------------------------------------------------------------------------
# probe_port
# --------------------------------------------------------------------------


def test_probe_reports_free_port():
    status, info = probe_port("127.0.0.1", _free_port())
    assert status == "free"
    assert info == {}


def test_probe_identifies_a_clay_daemon(live_daemon):
    status, info = probe_port("127.0.0.1", live_daemon)
    assert status == "clay"
    assert info["service"] == "clay-backend-webhook"
    assert info["db_path"] == database.get_db_path()


def test_probe_identifies_a_foreign_process(foreign_server):
    status, info = probe_port("127.0.0.1", foreign_server)
    assert status == "other"
    assert info == {}


# --------------------------------------------------------------------------
# ensure_webhook_server — the coexistence decision
# --------------------------------------------------------------------------


def test_mcp_defers_to_a_running_daemon(live_daemon, monkeypatch, caplog):
    """A daemon on the port means: do not start a second receiver."""
    monkeypatch.setenv("WEBHOOK_HOST", "127.0.0.1")
    result = ensure_webhook_server(port=live_daemon)

    assert result["status"] == "external"
    assert result["port"] == live_daemon
    assert "not starting a second receiver" in result["message"]
    # Same data dir in this test, so no split-brain warning.
    assert result["daemon_db_path"] == database.get_db_path()
    assert "warning" not in result


def test_mcp_warns_when_the_daemon_writes_elsewhere(clay_db, monkeypatch):
    """The silent split-brain becomes a loud, actionable warning.

    A real daemon on another data dir needs a second process, so the probe is
    stubbed to report exactly what such a daemon's /health would say.
    """
    import clay_backend.webhook_server as webhook_server

    foreign_db = "/some/other/place/clay.db"
    monkeypatch.setattr(
        webhook_server,
        "probe_port",
        lambda host, port, timeout=0.75: (
            "clay",
            {"service": "clay-backend-webhook", "db_path": foreign_db},
        ),
    )

    result = ensure_webhook_server(port=9999)

    assert result["status"] == "external"
    assert result["daemon_db_path"] == foreign_db
    assert "warning" in result
    assert foreign_db in result["warning"]
    assert database.get_db_path() in result["warning"]
    assert "CLAY_DATA_DIR" in result["warning"]


def test_mcp_reports_a_foreign_port_conflict(foreign_server, monkeypatch):
    monkeypatch.setenv("WEBHOOK_HOST", "127.0.0.1")
    result = ensure_webhook_server(port=foreign_server)

    assert result["status"] == "conflict"
    assert "WEBHOOK_PORT" in result["message"]


def test_mcp_starts_its_own_server_on_a_free_port(clay_db, monkeypatch):
    monkeypatch.setenv("WEBHOOK_HOST", "127.0.0.1")
    port = _free_port()
    result = ensure_webhook_server(port=port)

    assert result["status"] == "started"
    assert result["thread"].is_alive()

    # It really is listening, and it really is us.
    deadline = time.time() + 10
    status = "free"
    while time.time() < deadline:
        status, info = probe_port("127.0.0.1", port)
        if status == "clay":
            break
        time.sleep(0.05)
    assert status == "clay"


def test_daemon_refuses_a_busy_port_with_an_actionable_message(
    live_daemon, monkeypatch, capsys
):
    """uvicorn prints a bare errno on a busy bind — the daemon pre-checks instead."""
    from clay_backend import daemon

    monkeypatch.setattr(
        "sys.argv",
        ["clay-webhook-daemon", "--port", str(live_daemon), "--host", "127.0.0.1"],
    )
    # If the guard fails to fire, this makes the test loud instead of hanging.
    monkeypatch.setattr(
        daemon.uvicorn, "run", lambda *a, **k: pytest.fail("should not have bound")
    )

    with pytest.raises(SystemExit) as exc:
        daemon.main()

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "already held by another clay-backend webhook receiver" in err
    assert database.get_db_path() in err
    assert f"--port {live_daemon + 1}" in err


def test_daemon_starts_on_a_free_port(clay_db, monkeypatch):
    """The happy path still reaches uvicorn, with the DB path announced first."""
    from clay_backend import daemon

    port = _free_port()
    launched = {}
    monkeypatch.setattr("sys.argv", ["clay-webhook-daemon", "--port", str(port)])
    monkeypatch.setattr(daemon.uvicorn, "run", lambda *a, **k: launched.update(k))

    daemon.main()

    assert launched["port"] == port


def test_ensure_uses_configured_port_when_none_given(monkeypatch):
    """WEBHOOK_PORT is read through the tolerant parser, placeholder included."""
    monkeypatch.setenv("WEBHOOK_HOST", "127.0.0.1")
    monkeypatch.setenv("WEBHOOK_PORT", "${user_config.WEBHOOK_PORT}")
    assert config.webhook_port() == config.DEFAULT_WEBHOOK_PORT


# --------------------------------------------------------------------------
# Degraded mode — Python without SQLite extension support
# --------------------------------------------------------------------------


class _PyenvConnection:
    """Mimics a pyenv CPython: no `enable_load_extension` attribute at all."""

    def __getattr__(self, name):
        if name == "enable_load_extension":
            raise AttributeError(
                "'sqlite3.Connection' object has no attribute 'enable_load_extension'"
            )
        raise AttributeError(name)


def test_try_load_vec_turns_attributeerror_into_an_actionable_reason():
    """The exact traceback from the field test, caught at the source."""
    loaded, reason = database._try_load_vec(_PyenvConnection())

    assert loaded is False
    assert reason == database.NO_EXTENSION_SUPPORT
    assert "pyenv" in reason
    assert "uv venv --python 3.12 --managed-python" in reason


@requires_vec
def test_try_load_vec_succeeds_on_a_normal_connection():
    db = sqlite3.connect(":memory:")
    try:
        loaded, reason = database._try_load_vec(db)
    finally:
        db.close()

    assert loaded is True
    assert reason == ""


@pytest.fixture()
def no_extension_support(monkeypatch, clay_db):
    """Force the sqlite-vec load to fail the way pyenv builds do."""
    monkeypatch.setattr(
        database, "_try_load_vec", lambda db: (False, database.NO_EXTENSION_SUPPORT)
    )
    monkeypatch.setattr(database, "_VEC_AVAILABLE", None)
    monkeypatch.setattr(database, "_VEC_REASON", "")
    yield
    database._VEC_AVAILABLE = None
    database._VEC_REASON = ""


def test_connection_still_works_without_extension_support(no_extension_support):
    """The original crash: get_connection() must not raise."""
    db = database.get_connection()
    try:
        db.execute("SELECT 1").fetchone()
    finally:
        db.close()

    assert database.vec_available() is False
    reason = database.vec_unavailable_reason()
    assert "pyenv" in reason
    assert "uv venv" in reason  # actionable fix, not a traceback


def test_init_db_succeeds_without_extension_support(no_extension_support):
    database.init_db()  # must not raise
    assert database.init_vec_table(384) is False


def test_ingest_degrades_to_no_embeddings(no_extension_support):
    """Records still land; only semantic search is lost."""
    from clay_backend.models import RecordInput
    from clay_backend.services import record_service

    database.init_db()
    result = record_service.ingest_records(
        [
            RecordInput(
                record_id="degraded-1",
                analysis_type="pyenv_test",
                data={"summary": "stored without embeddings"},
            )
        ]
    )

    assert result.ingested == 1
    assert result.errors == []

    stored = record_service.query_records(analysis_type="pyenv_test")
    assert len(stored) == 1
    assert stored[0].embedding_model is None

    # Text search — the documented fallback — still answers.
    assert record_service.query_records(search_data="without embeddings")


def test_semantic_search_explains_itself_when_vec_is_unavailable(no_extension_support):
    from clay_backend.services import search_service

    database.init_db()
    results = search_service.semantic_search("anything at all")

    assert len(results) == 1
    assert "pyenv" in results[0]["error"]
    assert "query_records" in results[0]["fallback"]
