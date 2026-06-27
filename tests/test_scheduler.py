"""Scheduler tests — _resolve_since(), _fetch_records(), and the Anthropic call.

The real Anthropic API is NEVER hit; the client is mocked. These tests also pin
down two findings (see FINDINGS.md): the stale default model id and the
fetch-everything-into-one-prompt behavior at scale.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from clay_backend import scheduler
from clay_backend.models import RecordInput
from clay_backend.services import record_service


# --------------------------------------------------------------------------- #
# _resolve_since
# --------------------------------------------------------------------------- #
def test_resolve_since_days():
    out = scheduler._resolve_since("7 days ago")
    parsed = datetime.fromisoformat(out)
    delta = datetime.now(timezone.utc) - parsed
    assert 6.9 < delta.total_seconds() / 86400 < 7.1


def test_resolve_since_units():
    now = datetime.now(timezone.utc)
    hours = datetime.fromisoformat(scheduler._resolve_since("3 hours ago"))
    assert 2.9 < (now - hours).total_seconds() / 3600 < 3.1

    weeks = datetime.fromisoformat(scheduler._resolve_since("2 weeks ago"))
    assert 13.5 < (now - weeks).total_seconds() / 86400 < 14.5

    months = datetime.fromisoformat(scheduler._resolve_since("1 month ago"))
    assert 29 < (now - months).total_seconds() / 86400 < 31


def test_resolve_since_passthrough_for_absolute_and_garbage():
    # Already-ISO or unparseable strings are returned unchanged.
    assert scheduler._resolve_since("2026-01-01T00:00:00+00:00") == "2026-01-01T00:00:00+00:00"
    assert scheduler._resolve_since("whenever soon") == "whenever soon"
    assert scheduler._resolve_since("nan days ago") == "nan days ago"


# --------------------------------------------------------------------------- #
# _fetch_records
# --------------------------------------------------------------------------- #
def test_fetch_records_filters_and_resolves_since(ingested):
    recs = scheduler._fetch_records({"analysis_type": "call_analysis"})
    assert len(recs) == 24
    assert all(r["analysis_type"] == "call_analysis" for r in recs)
    # Returns plain dicts (model_dump), ready for JSON serialization.
    assert isinstance(recs[0], dict)
    assert "data" in recs[0]


def test_fetch_records_relative_since_returns_recent(ingested):
    # All fixtures were just ingested (created_at ~ now), so "1 day ago" keeps them.
    recent = scheduler._fetch_records({"since": "1 day ago"})
    assert len(recent) > 0
    # A far-future-relative window in the past excludes them.
    none_recent = scheduler._fetch_records(
        {"since": "2026-01-01T00:00:00+00:00", "until": "2026-01-02T00:00:00+00:00"}
    )
    assert none_recent == []


# --------------------------------------------------------------------------- #
# _call_anthropic (mocked)
# --------------------------------------------------------------------------- #
class _FakeContentBlock:
    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeContentBlock(text)]


class _FakeMessages:
    def __init__(self, recorder):
        self._recorder = recorder

    def create(self, **kwargs):
        self._recorder.update(kwargs)
        return _FakeResponse("SYNTHESIZED INSIGHT")


class _FakeClient:
    def __init__(self, recorder):
        self.messages = _FakeMessages(recorder)


def test_call_anthropic_builds_prompt_and_uses_default_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    recorder: dict = {}
    monkeypatch.setattr(
        scheduler.anthropic, "Anthropic", lambda api_key: _FakeClient(recorder)
    )

    records = [
        {"record_id": "call-001", "data": {"summary": "alpha budget approved"}},
        {"record_id": "call-002", "data": {"summary": "beta ghosting risk"}},
    ]
    schedule = {"name": "weekly", "context": {"analysis_type": "call_analysis", "since": "7 days ago"}}

    out = scheduler._call_anthropic("SYSTEM PROMPT", records, schedule)
    assert out == "SYNTHESIZED INSIGHT"

    # Default model when schedule doesn't override (FINDINGS: stale id).
    assert recorder["model"] == "claude-sonnet-4-20250514"
    assert recorder["system"] == "SYSTEM PROMPT"

    # ALL records are dumped into a single user message (FINDINGS: no chunking).
    user_msg = recorder["messages"][0]["content"]
    assert "alpha budget approved" in user_msg
    assert "beta ghosting risk" in user_msg
    assert "Record count: 2" in user_msg


def test_call_anthropic_requires_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        scheduler._call_anthropic("p", [], {"name": "x"})


def test_call_anthropic_dumps_all_records_no_truncation(monkeypatch):
    """At scale the scheduler serializes EVERY record into one prompt with no
    token budgeting — this test documents that behavior (see FINDINGS.md)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    recorder: dict = {}
    monkeypatch.setattr(
        scheduler.anthropic, "Anthropic", lambda api_key: _FakeClient(recorder)
    )

    big = [
        {"record_id": f"r{i}", "data": {"summary": f"unique-marker-{i} " + "x" * 200}}
        for i in range(200)
    ]
    scheduler._call_anthropic("sys", big, {"name": "big"})

    user_msg = recorder["messages"][0]["content"]
    # Every single record's marker is present — nothing was dropped or truncated.
    assert all(f"unique-marker-{i}" in user_msg for i in range(200))
    assert len(user_msg) > 200 * 200  # whole corpus inlined
