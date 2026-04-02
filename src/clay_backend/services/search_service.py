"""Semantic search service using sqlite-vec."""

from __future__ import annotations

import json

from ..database import get_connection, get_vec_dimension
from ..models import Record
from . import embedding_service


def _row_to_record(row: dict) -> Record:
    tags = json.loads(row["tags"]) if row["tags"] else []
    data = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
    return Record(
        id=row["id"],
        record_id=row["record_id"],
        analysis_type=row["analysis_type"],
        data=data,
        source=row["source"],
        entity_id=row["entity_id"],
        entity_name=row["entity_name"],
        tags=tags,
        embedding_model=row["embedding_model"],
        created_at=row["created_at"],
        received_at=row["received_at"],
    )


def semantic_search(
    query: str,
    analysis_type: str | None = None,
    top_k: int = 10,
) -> list[dict]:
    """Search records by semantic similarity to the query text.

    Returns records ranked by similarity with scores.
    """
    # Check if embeddings are available
    if get_vec_dimension() is None:
        return [{"error": "Embeddings not initialized. Set EMBEDDING_PROVIDER and re-ingest."}]

    query_embedding = embedding_service.embed_text(query)
    if query_embedding is None:
        msg = "Embedding provider not configured. Set EMBEDDING_PROVIDER to 'openai' or 'local'."
        return [{"error": msg}]

    top_k = min(top_k, 50)
    embedding_json = json.dumps(query_embedding)

    db = get_connection()
    try:
        # sqlite-vec requires `k = ?` in WHERE for KNN, not LIMIT
        # Fetch more candidates if filtering by type (some may be filtered out)
        fetch_k = top_k * 3 if analysis_type else top_k

        vec_rows = db.execute(
            """
            SELECT record_id, distance
            FROM vec_records
            WHERE embedding MATCH ? AND k = ?
            ORDER BY distance
            """,
            (embedding_json, fetch_k),
        ).fetchall()

        results = []
        for vec_row in vec_rows:
            rec_id = vec_row["record_id"]
            distance = vec_row["distance"]

            row = db.execute(
                "SELECT * FROM analysis_records WHERE id = ?", (rec_id,)
            ).fetchone()

            if row is None:
                continue

            if analysis_type and row["analysis_type"] != analysis_type:
                continue

            similarity = round(1.0 / (1.0 + distance), 4)
            record = _row_to_record(dict(row))
            results.append({
                "record": record.model_dump(),
                "similarity_score": similarity,
            })

            if len(results) >= top_k:
                break

        return results
    finally:
        db.close()
