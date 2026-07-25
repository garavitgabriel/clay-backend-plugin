"""Diagnostics: `clay-backend-doctor` / the `doctor` MCP tool.

The doctor exists to catch the four field-test bugs in ten seconds, so the
tests here assert it actually notices each condition rather than just running
without raising.
"""

from __future__ import annotations

import socket
import threading

import pytest

from clay_backend import database, doctor


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _by_name(report: dict, name: str) -> dict:
    for check in report["checks"]:
        if check["check"] == name:
            return check
    raise AssertionError(f"no check named {name!r} in {report}")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, clay_db):
    """Local mode, free webhook port, no leftover remote/auth config."""
    monkeypatch.delenv("REMOTE_URL", raising=False)
    monkeypatch.delenv("REMOTE_API_KEY", raising=False)
    monkeypatch.delenv("WEBHOOK_API_KEY", raising=False)
    monkeypatch.setenv("WEBHOOK_HOST", "127.0.0.1")
    monkeypatch.setenv("WEBHOOK_PORT", str(_free_port()))


def test_report_shape():
    report = doctor.run_checks()

    assert report["overall"] in (doctor.PASS, doctor.WARN, doctor.FAIL)
    assert set(report["summary"]) == {doctor.PASS, doctor.WARN, doctor.FAIL}
    assert len(report["checks"]) == len(doctor.CHECKS)
    for check in report["checks"]:
        assert check["status"] in (doctor.PASS, doctor.WARN, doctor.FAIL)
        assert check["detail"]


def test_report_is_printable():
    text = doctor.format_report(doctor.run_checks())
    assert "clay-backend doctor" in text
    assert "passed" in text


def test_database_check_reports_the_resolved_path_and_counts(ingested):
    report = doctor.run_checks()
    check = _by_name(report, "database")

    assert check["status"] == doctor.PASS
    assert database.get_db_path() in check["detail"]
    assert "call_analysis=24" in check["detail"]


def test_database_check_warns_on_an_empty_store():
    check = _by_name(doctor.run_checks(), "database")

    assert check["status"] == doctor.WARN
    # The "0 records" failure mode points at the split-brain cause.
    assert "daemon" in check["fix"]


def test_port_check_passes_when_free():
    check = _by_name(doctor.run_checks(), "webhook port")
    assert check["status"] == doctor.PASS
    assert "free" in check["detail"]


def test_port_check_warns_on_a_foreign_process(monkeypatch):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
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
    try:
        monkeypatch.setenv("WEBHOOK_PORT", str(port))
        check = _by_name(doctor.run_checks(), "webhook port")
        assert check["status"] == doctor.WARN
        assert "not a clay-backend receiver" in check["detail"]
    finally:
        stop.set()
        thread.join(timeout=2)
        sock.close()


def test_extension_check_reports_degraded_mode(monkeypatch):
    monkeypatch.setattr(
        database, "_try_load_vec", lambda db: (False, database.NO_EXTENSION_SUPPORT)
    )
    monkeypatch.setattr(database, "_VEC_AVAILABLE", None)
    monkeypatch.setattr(database, "_VEC_REASON", "")
    try:
        check = _by_name(doctor.run_checks(), "sqlite extension support")
        assert check["status"] == doctor.WARN
        assert "pyenv" in check["detail"] + check.get("fix", "")
        assert "uv venv --python 3.12 --managed-python" in check["fix"]
    finally:
        database._VEC_AVAILABLE = None
        database._VEC_REASON = ""


def test_embeddings_check_fails_on_openai_without_a_key(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    check = _by_name(doctor.run_checks(), "embeddings")
    assert check["status"] == doctor.FAIL
    assert "OPENAI_API_KEY" in check["detail"]
    assert doctor.run_checks()["overall"] == doctor.FAIL


def test_embeddings_check_treats_unset_as_a_fine_default(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "${user_config.EMBEDDING_PROVIDER}")
    monkeypatch.setenv("OPENAI_API_KEY", "${user_config.OPENAI_API_KEY}")

    check = _by_name(doctor.run_checks(), "embeddings")
    assert check["status"] == doctor.PASS
    assert "disabled" in check["detail"]


def test_auth_check_flags_an_unauthenticated_wide_bind(monkeypatch):
    monkeypatch.setenv("WEBHOOK_HOST", "0.0.0.0")
    monkeypatch.delenv("WEBHOOK_API_KEY", raising=False)

    check = _by_name(doctor.run_checks(), "webhook auth")
    assert check["status"] == doctor.WARN
    assert "WEBHOOK_API_KEY" in check["fix"]


def test_auth_check_passes_with_a_key_set(monkeypatch):
    monkeypatch.setenv("WEBHOOK_API_KEY", "s3cret")

    check = _by_name(doctor.run_checks(), "webhook auth")
    assert check["status"] == doctor.PASS
    assert "Bearer" in check["detail"]


def test_remote_mode_skips_local_checks(monkeypatch):
    monkeypatch.setenv("REMOTE_URL", "https://example.railway.app")

    report = doctor.run_checks()
    assert "remote" in _by_name(report, "mode")["detail"]
    assert "skipped" in _by_name(report, "database")["detail"]
    assert "skipped" in _by_name(report, "webhook port")["detail"]


def test_cli_exit_code_is_zero_when_nothing_fails(capsys):
    assert doctor.main() == 0
    assert "clay-backend doctor" in capsys.readouterr().out


def test_cli_exit_code_is_one_on_failure(monkeypatch, capsys):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert doctor.main() == 1
    capsys.readouterr()
