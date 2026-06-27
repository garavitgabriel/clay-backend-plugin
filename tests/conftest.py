"""Shared pytest fixtures for the clay-backend-plugin local-mode test suite.

Critical gotchas handled here (see CLAUDE.md + FINDINGS.md):

  * database._DB_PATH is a module-level cache — it must be reset whenever
    CLAY_DATA_DIR changes, or every test writes to the first test's db.
  * embedding_service._provider / _initialized are module-level caches. The
    local sentence-transformers model is expensive to load, so we load it ONCE
    (session scope) and keep it; only the DB path is reset per test.
  * EMBEDDING_PROVIDER must be 'local' BEFORE the provider is first resolved.
  * REMOTE_URL must be unset so remote_service.is_remote() stays False.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# Force local mode + local embeddings before any clay_backend import resolves them.
os.environ["EMBEDDING_PROVIDER"] = "local"
os.environ.pop("REMOTE_URL", None)
os.environ.pop("OPENAI_API_KEY", None)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixture_records() -> list[dict]:
    """The 48 synthetic Clay records (24 call_analysis + 24 company_enrichment)."""
    data = json.loads((FIXTURES / "clay_records.json").read_text())
    # Strip the helper-only _created_at key; it is not part of RecordInput.
    for r in data:
        r.pop("_created_at", None)
    return data


@pytest.fixture(scope="session")
def csv_path() -> str:
    return str(FIXTURES / "call_analysis.csv")


@pytest.fixture()
def clay_db(tmp_path, monkeypatch):
    """Fresh, isolated SQLite database per test.

    Resets the cached _DB_PATH so CLAY_DATA_DIR actually takes effect, then
    initializes the schema. The local embedding provider is intentionally NOT
    reset — the model loads once and is reused across the whole session.
    """
    import clay_backend.database as database

    data_dir = tmp_path / "clay_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CLAY_DATA_DIR", str(data_dir))
    monkeypatch.setattr(database, "_DB_PATH", None)

    database.init_db()
    yield data_dir

    # Drop the cached path so the next test re-resolves against its own tmp_path.
    database._DB_PATH = None


@pytest.fixture()
def ingested(clay_db, fixture_records):
    """A db pre-loaded with all 48 fixture records (with embeddings)."""
    from clay_backend.models import RecordInput
    from clay_backend.services import record_service

    parsed = [RecordInput(**r) for r in fixture_records]
    result = record_service.ingest_records(parsed)
    assert result.ingested == 48, f"setup ingest failed: {result.errors}"
    return clay_db
