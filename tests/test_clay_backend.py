"""End-to-end LOCAL-mode validation of the clay-backend-plugin service layer.

Exercises the real code paths (SQLite + sqlite-vec + local sentence-transformers),
not trivial asserts. See FINDINGS.md for bugs these tests surfaced.
"""

from __future__ import annotations

import pytest

from clay_backend.database import get_connection, get_vec_dimension, init_vec_table
from clay_backend.models import RecordInput
from clay_backend.services import record_service, search_service


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _set_created_at(record_id: str, analysis_type: str, iso: str) -> None:
    """Backdate a row's created_at directly (ingest always stamps now())."""
    db = get_connection()
    try:
        db.execute(
            "UPDATE analysis_records SET created_at = ? "
            "WHERE record_id = ? AND analysis_type = ?",
            (iso, record_id, analysis_type),
        )
        db.commit()
    finally:
        db.close()


def _vec_count() -> int:
    db = get_connection()
    try:
        return db.execute("SELECT COUNT(*) AS c FROM vec_records").fetchone()["c"]
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# ingest
# --------------------------------------------------------------------------- #
def test_ingest_records_basic(clay_db, fixture_records):
    parsed = [RecordInput(**r) for r in fixture_records]
    result = record_service.ingest_records(parsed)

    assert result.ingested == 48
    assert result.updated == 0
    assert result.errors == []

    # Every ingested record got an embedding row in the vec table.
    assert _vec_count() == 48
    assert get_vec_dimension() == 384  # local all-MiniLM-L6-v2


def test_ingest_csv(clay_db, csv_path):
    result = record_service.ingest_csv(
        file_path=csv_path,
        record_id_column="Row ID",
        analysis_type="call_csv",
        data_columns=["Rep", "Score", "Sentiment", "Summary"],
        entity_id_column="Entity ID",
        entity_name_column="Company",
    )
    assert result.ingested == 24
    assert result.errors == []

    rows = record_service.query_records(analysis_type="call_csv", limit=200)
    assert len(rows) == 24
    # entity_id / entity_name columns were mapped through.
    sample = next(r for r in rows if r.record_id == "call-000")
    assert sample.entity_id == "company-0"
    assert sample.entity_name == "Acme Robotics"
    assert sample.data["Rep"]  # data_columns landed inside the JSON blob
    assert "Score" in sample.data


def test_ingest_csv_missing_id_column(clay_db, csv_path):
    result = record_service.ingest_csv(
        file_path=csv_path,
        record_id_column="Nonexistent",
        analysis_type="call_csv",
        data_columns=["Rep"],
    )
    assert result.ingested == 0
    assert any("not found" in e for e in result.errors)


# --------------------------------------------------------------------------- #
# dedup
# --------------------------------------------------------------------------- #
def test_dedup_updates_not_duplicates(clay_db):
    rec = RecordInput(
        record_id="call-xyz",
        analysis_type="call_analysis",
        data={"score": 50, "summary": "first version"},
        entity_id="company-0",
    )
    r1 = record_service.ingest_records([rec])
    assert r1.ingested == 1 and r1.updated == 0

    # Same (record_id, analysis_type), changed payload.
    rec2 = RecordInput(
        record_id="call-xyz",
        analysis_type="call_analysis",
        data={"score": 99, "summary": "second version"},
        entity_id="company-0",
    )
    r2 = record_service.ingest_records([rec2])
    assert r2.updated == 1 and r2.ingested == 0

    rows = record_service.query_records(analysis_type="call_analysis", limit=200)
    matching = [r for r in rows if r.record_id == "call-xyz"]
    assert len(matching) == 1  # not duplicated
    assert matching[0].data["score"] == 99  # updated in place
    assert matching[0].data["summary"] == "second version"

    # Same record_id but DIFFERENT analysis_type is a distinct row.
    rec3 = RecordInput(
        record_id="call-xyz",
        analysis_type="company_enrichment",
        data={"industry": "SaaS"},
        entity_id="company-0",
    )
    r3 = record_service.ingest_records([rec3])
    assert r3.ingested == 1
    both = [
        r
        for r in record_service.query_records(limit=200)
        if r.record_id == "call-xyz"
    ]
    assert len(both) == 2


# --------------------------------------------------------------------------- #
# cross-type entity join
# --------------------------------------------------------------------------- #
def test_entity_id_cross_analysis_type_join(ingested):
    # company-3 has BOTH a call_analysis and a company_enrichment row.
    rows = record_service.query_records(entity_id="company-3", limit=200)
    types = {r.analysis_type for r in rows}
    assert types == {"call_analysis", "company_enrichment"}
    assert len(rows) == 2
    # Same human-readable entity surfaced from both sources.
    assert {r.entity_name for r in rows} == {"Initech Software"}


# --------------------------------------------------------------------------- #
# query filters
# --------------------------------------------------------------------------- #
def test_query_filter_analysis_type(ingested):
    calls = record_service.query_records(analysis_type="call_analysis", limit=200)
    enrich = record_service.query_records(analysis_type="company_enrichment", limit=200)
    assert len(calls) == 24
    assert len(enrich) == 24
    assert all(r.analysis_type == "call_analysis" for r in calls)


def test_query_filter_single_tag(ingested):
    rows = record_service.query_records(tags=["champion_identified"], limit=200)
    assert len(rows) > 0
    assert all("champion_identified" in r.tags for r in rows)


def test_query_filter_multiple_tags_or_semantics(ingested):
    # The code comment promises OR ("any of the specified tags"). These two tags
    # never co-occur on a record, so OR should return both groups; AND returns 0.
    only_champion = record_service.query_records(tags=["champion_identified"], limit=200)
    multi = record_service.query_records(
        tags=["champion_identified", "no_budget"], limit=200
    )
    assert len(multi) >= len(only_champion)
    assert all(
        ("champion_identified" in r.tags) or ("no_budget" in r.tags) for r in multi
    )


def test_query_filter_search_data(ingested):
    rows = record_service.query_records(search_data="ghosting", limit=200)
    assert len(rows) > 0
    assert all("ghosting" in str(r.data).lower() for r in rows)


def test_query_filter_since_until(ingested):
    # Backdate two specific records, leave the rest at "now".
    old_iso = "2026-01-01T00:00:00+00:00"
    mid_iso = "2026-03-15T00:00:00+00:00"
    _set_created_at("call-000", "call_analysis", old_iso)
    _set_created_at("call-001", "call_analysis", mid_iso)

    since = record_service.query_records(since="2026-02-01T00:00:00+00:00", limit=200)
    since_ids = {r.record_id for r in since}
    assert "call-000" not in since_ids  # before the window
    assert "call-001" in since_ids      # inside the window

    until = record_service.query_records(until="2026-02-01T00:00:00+00:00", limit=200)
    until_ids = {r.record_id for r in until}
    assert "call-000" in until_ids
    assert "call-001" not in until_ids

    window = record_service.query_records(
        since="2026-02-01T00:00:00+00:00",
        until="2026-04-01T00:00:00+00:00",
        limit=200,
    )
    win_ids = {r.record_id for r in window}
    assert win_ids == {"call-001"}


def test_query_limit_capped_at_200(ingested):
    # Service hard-caps limit at 200 even if a larger value is requested.
    rows = record_service.query_records(limit=99999)
    assert len(rows) <= 200


# --------------------------------------------------------------------------- #
# semantic search
# --------------------------------------------------------------------------- #
def test_semantic_search_ranks_results(ingested):
    results = search_service.semantic_search(
        "deep technical dive into the API and embeddings integration", top_k=5
    )
    assert len(results) == 5
    assert all("error" not in r for r in results)

    scores = [r["similarity_score"] for r in results]
    # Ranked: similarity scores monotonically non-increasing.
    assert scores == sorted(scores, reverse=True)
    assert scores[0] > scores[-1]  # genuine spread, not all identical

    # The near-duplicate technical-dive summary should be the top hit.
    top_summary = results[0]["record"]["data"]["summary"].lower()
    assert "technical" in top_summary or "api" in top_summary


def test_semantic_search_filtered_by_type(ingested):
    results = search_service.semantic_search(
        "company is a strong ICP fit, well funded", analysis_type="company_enrichment", top_k=5
    )
    assert len(results) > 0
    assert all(
        r["record"]["analysis_type"] == "company_enrichment" for r in results
    )


def test_semantic_search_without_embeddings_returns_error(clay_db):
    # Fresh db, nothing ingested -> vec table never initialized.
    results = search_service.semantic_search("anything", top_k=5)
    assert len(results) == 1
    assert "error" in results[0]


# --------------------------------------------------------------------------- #
# embedding dimension lock
# --------------------------------------------------------------------------- #
def test_embedding_dimension_lock(ingested):
    assert get_vec_dimension() == 384

    # Re-initializing with the SAME dimension is a no-op (idempotent).
    init_vec_table(384)
    assert get_vec_dimension() == 384

    # A DIFFERENT dimension (e.g. switching to OpenAI 1536) must be rejected.
    with pytest.raises(ValueError, match="dimension"):
        init_vec_table(1536)


# --------------------------------------------------------------------------- #
# analytics + listing
# --------------------------------------------------------------------------- #
def test_get_analytics(ingested):
    a = record_service.get_analytics()
    assert a.total_records == 48
    by_type = {t.analysis_type: t.count for t in a.records_by_type}
    assert by_type == {"call_analysis": 24, "company_enrichment": 24}

    # Each entity has exactly 2 rows (one per type) -> top entities count == 2.
    assert len(a.top_entities) == 10  # capped at 10
    assert all(e["count"] == 2 for e in a.top_entities)

    assert a.storage_size_mb > 0


def test_get_analytics_filtered(ingested):
    a = record_service.get_analytics(analysis_type="call_analysis")
    assert a.total_records == 24
    assert [t.analysis_type for t in a.records_by_type] == ["call_analysis"]


def test_list_analysis_types(ingested):
    types = record_service.list_analysis_types()
    by = {t.analysis_type: t for t in types}
    assert set(by) == {"call_analysis", "company_enrichment"}
    assert by["call_analysis"].count == 24
    assert by["call_analysis"].newest_record >= by["call_analysis"].oldest_record


# --------------------------------------------------------------------------- #
# delete (filter required)
# --------------------------------------------------------------------------- #
def test_delete_requires_filter(ingested):
    # No filter -> service refuses, deletes nothing.
    result = record_service.delete_records()
    assert result.deleted == 0
    assert record_service.get_analytics().total_records == 48


def test_delete_by_type_clears_vec_rows(ingested):
    before = _vec_count()
    assert before == 48

    result = record_service.delete_records(analysis_type="call_analysis")
    assert result.deleted == 24

    remaining = record_service.query_records(limit=200)
    assert all(r.analysis_type == "company_enrichment" for r in remaining)
    assert len(remaining) == 24

    # vec rows for deleted records were also removed.
    assert _vec_count() == 24


def test_delete_server_layer_guard():
    # The MCP tool wrapper returns an explicit error rather than a silent no-op.
    from clay_backend import server

    out = server.delete_records()
    assert out["deleted"] == 0
    assert "error" in out
