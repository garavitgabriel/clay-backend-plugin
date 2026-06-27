"""HTTP webhook server tests (in-process, via Starlette TestClient).

Covers POST /webhook ingest, batch handling, and the WEBHOOK_API_KEY auth path.
The TestClient runs in the same process, so it shares the module-level DB path
set by the clay_db fixture.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from clay_backend.services import record_service
from clay_backend.webhook_server import app


@pytest.fixture()
def client():
    return TestClient(app)


def test_health(client, clay_db):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_webhook_single_record(client, clay_db):
    payload = {
        "record_id": "wh-001",
        "analysis_type": "call_analysis",
        "data": {"score": 88, "summary": "webhook delivered call"},
        "entity_id": "company-99",
        "entity_name": "Webhook Co",
    }
    resp = client.post("/webhook", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ingested"] == 1
    assert body["errors"] == []

    stored = record_service.query_records(analysis_type="call_analysis", limit=10)
    assert any(r.record_id == "wh-001" for r in stored)


def test_webhook_batch_records(client, clay_db):
    payload = {
        "records": [
            {"record_id": f"wh-b{i}", "analysis_type": "batch_type", "data": {"i": i}}
            for i in range(5)
        ]
    }
    resp = client.post("/webhook", json=payload)
    assert resp.status_code == 200
    assert resp.json()["ingested"] == 5
    assert len(record_service.query_records(analysis_type="batch_type", limit=50)) == 5


def test_webhook_invalid_record_reports_error(client, clay_db):
    # Missing required analysis_type -> validation error, 400 when nothing parses.
    resp = client.post("/webhook", json={"record_id": "bad", "data": {}})
    assert resp.status_code == 400
    assert resp.json()["errors"]


def test_webhook_auth_required_when_key_set(client, clay_db, monkeypatch):
    monkeypatch.setenv("WEBHOOK_API_KEY", "s3cret")
    payload = {"record_id": "auth-1", "analysis_type": "t", "data": {"x": 1}}

    # No credentials -> 401
    resp = client.post("/webhook", json=payload)
    assert resp.status_code == 401
    assert resp.json()["error"] == "Unauthorized"

    # Wrong credentials -> 401
    resp = client.post(
        "/webhook", json=payload, headers={"Authorization": "Bearer wrong"}
    )
    assert resp.status_code == 401

    # Correct Bearer token -> 200
    resp = client.post(
        "/webhook", json=payload, headers={"Authorization": "Bearer s3cret"}
    )
    assert resp.status_code == 200
    assert resp.json()["ingested"] == 1

    # Correct X-API-Key header -> 200
    payload2 = {"record_id": "auth-2", "analysis_type": "t", "data": {"x": 2}}
    resp = client.post("/webhook", json=payload2, headers={"X-API-Key": "s3cret"})
    assert resp.status_code == 200
    assert resp.json()["ingested"] == 1


def test_webhook_no_auth_when_key_unset(client, clay_db, monkeypatch):
    monkeypatch.delenv("WEBHOOK_API_KEY", raising=False)
    resp = client.post(
        "/webhook", json={"record_id": "open-1", "analysis_type": "t", "data": {}}
    )
    assert resp.status_code == 200
