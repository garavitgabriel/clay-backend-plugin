"""Scheduler tests — _resolve_since(), _fetch_records(), and the Anthropic call.

The real Anthropic API is NEVER hit; the client is mocked. These tests also pin
down two findings (see FINDINGS.md): the stale default model id and the
fetch-everything-into-one-prompt behavior at scale.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from clay_backend import scheduler


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
    schedule = {
        "name": "weekly",
        "context": {"analysis_type": "call_analysis", "since": "7 days ago"},
    }

    out = scheduler._call_anthropic("SYSTEM PROMPT", records, schedule)
    assert out == "SYNTHESIZED INSIGHT"

    # Default model when schedule doesn't override — current Sonnet, env-overridable
    # (FINDINGS #3 fixed: no longer pinned to a stale dated snapshot).
    assert recorder["model"] == scheduler.DEFAULT_MODEL
    assert recorder["model"] != "claude-sonnet-4-20250514"
    assert recorder["system"] == "SYSTEM PROMPT"

    # Records within budget are all included in the user message.
    user_msg = recorder["messages"][0]["content"]
    assert "alpha budget approved" in user_msg
    assert "beta ghosting risk" in user_msg
    assert "Record count: 2" in user_msg


def test_call_anthropic_requires_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        scheduler._call_anthropic("p", [], {"name": "x"})


def test_call_anthropic_keeps_all_records_within_budget(monkeypatch):
    """Records that fit the prompt budget are all serialized — no silent drops."""
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
    assert all(f"unique-marker-{i}" in user_msg for i in range(200))
    assert "omitted" not in user_msg


def test_call_anthropic_truncates_over_budget_with_note(monkeypatch, caplog):
    """Over-budget record sets are truncated (oldest dropped) and the prompt
    carries an explicit omission note — FINDINGS #2 mitigation."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(scheduler, "PROMPT_CHAR_BUDGET", 20_000)
    recorder: dict = {}
    monkeypatch.setattr(
        scheduler.anthropic, "Anthropic", lambda api_key: _FakeClient(recorder)
    )

    big = [
        {"record_id": f"r{i}", "data": {"summary": f"unique-marker-{i} " + "x" * 200}}
        for i in range(200)
    ]
    with caplog.at_level("WARNING"):
        scheduler._call_anthropic("sys", big, {"name": "big"})

    user_msg = recorder["messages"][0]["content"]
    # Newest records kept, oldest dropped, and the omission is stated in-prompt.
    assert "unique-marker-0" in user_msg
    assert "unique-marker-199" not in user_msg
    assert "omitted" in user_msg
    assert len(user_msg) < 30_000
    assert any("omitted" in r.message for r in caplog.records)


def test_fetch_records_warns_on_cap(ingested, caplog):
    """Hitting the fetch limit logs a truncation warning — FINDINGS #2 mitigation."""
    with caplog.at_level("WARNING"):
        recs = scheduler._fetch_records({"limit": 10})
    assert len(recs) == 10
    assert any("cap" in r.message.lower() for r in caplog.records)
