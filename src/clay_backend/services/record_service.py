"""Service for storing, querying, and managing analysis records."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from ..database import get_connection, get_vec_dimension, init_vec_table
from ..models import (
    AnalysisTypeSummary,
    AnalyticsSummary,
    DeleteResult,
    IngestResult,
    Record,
    RecordInput,
)
from . import embedding_service


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def ingest_records(
    records: list[RecordInput],
    embed_fields: list[str] | None = None,
) -> IngestResult:
    """Store records with upsert and optional embedding generation."""
    db = get_connection()
    result = IngestResult()

    # Check if embeddings are available and ensure vec table exists
    provider = embedding_service.get_provider()
    if provider is not None:
        dimension = provider.dimension
        if get_vec_dimension() is None:
            init_vec_table(dimension)
        model_name = provider.model_name
    else:
        model_name = None

    # Collect texts for batch embedding
    record_uuids: list[str] = []
    embed_texts: list[str] = []

    try:
        for rec in records:
            try:
                record_uuid = str(uuid.uuid4())
                now = _now_iso()
                tags_json = json.dumps(rec.tags)
                data_json = json.dumps(rec.data)

                # Try insert, on conflict update
                db.execute(
                    """
                    INSERT INTO analysis_records
                        (id, record_id, analysis_type, data, source,
                         entity_id, entity_name, tags, created_at, received_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (record_id, analysis_type) DO UPDATE SET
                        data = excluded.data,
                        source = excluded.source,
                        entity_id = excluded.entity_id,
                        entity_name = excluded.entity_name,
                        tags = excluded.tags,
                        received_at = excluded.received_at
                    """,
                    (
                        record_uuid,
                        rec.record_id,
                        rec.analysis_type,
                        data_json,
                        rec.source,
                        rec.entity_id,
                        rec.entity_name,
                        tags_json,
                        now,
                        now,
                    ),
                )

                # Determine actual UUID (insert or existing)
                existing = db.execute(
                    "SELECT id FROM analysis_records WHERE record_id = ? AND analysis_type = ?",
                    (rec.record_id, rec.analysis_type),
                ).fetchone()
                actual_uuid = existing["id"]

                if actual_uuid == record_uuid:
                    result.ingested += 1
                else:
                    result.updated += 1

                # Prepare text for embedding
                if provider is not None:
                    text = embedding_service.extract_text(rec.data, embed_fields)
                    record_uuids.append(actual_uuid)
                    embed_texts.append(text)

            except Exception as e:
                result.errors.append(f"Record {rec.record_id}: {e}")

        db.commit()
    finally:
        db.close()

    # Generate embeddings in batch (after commit so records exist)
    if provider is not None and embed_texts:
        try:
            embeddings = provider.embed_batch(embed_texts)
            db = get_connection()
            try:
                for record_uuid, text, embedding in zip(record_uuids, embed_texts, embeddings):
                    embedding_json = json.dumps(embedding)
                    # Update embedding metadata on the record
                    db.execute(
                        """
                        UPDATE analysis_records
                        SET embedding_model = ?, embedding_text = ?
                        WHERE id = ?
                        """,
                        (model_name, text, record_uuid),
                    )
                    # Upsert into vec table
                    db.execute(
                        "DELETE FROM vec_records WHERE record_id = ?", (record_uuid,)
                    )
                    db.execute(
                        "INSERT INTO vec_records (record_id, embedding) VALUES (?, ?)",
                        (record_uuid, embedding_json),
                    )
                db.commit()
            finally:
                db.close()
        except Exception as e:
            result.errors.append(f"Embedding generation failed: {e}")

    return result


def ingest_csv(
    file_path: str,
    record_id_column: str,
    analysis_type: str,
    data_columns: list[str],
    entity_id_column: str | None = None,
    entity_name_column: str | None = None,
    embed_fields: list[str] | None = None,
) -> IngestResult:
    """Import records from a CSV file."""
    import csv

    records: list[RecordInput] = []

    with open(file_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if record_id_column not in row:
                return IngestResult(errors=[f"Column '{record_id_column}' not found in CSV"])

            data = {col: row.get(col, "") for col in data_columns if col in row}
            record = RecordInput(
                record_id=row[record_id_column],
                analysis_type=analysis_type,
                data=data,
                entity_id=row.get(entity_id_column) if entity_id_column else None,
                entity_name=row.get(entity_name_column) if entity_name_column else None,
            )
            records.append(record)

    return ingest_records(records, embed_fields=embed_fields)


def query_records(
    analysis_type: str | None = None,
    entity_id: str | None = None,
    tags: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    search_data: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Record]:
    """Query records with optional filters."""
    db = get_connection()
    try:
        conditions: list[str] = []
        params: list = []

        if analysis_type:
            conditions.append("analysis_type = ?")
            params.append(analysis_type)
        if entity_id:
            conditions.append("entity_id = ?")
            params.append(entity_id)
        if since:
            conditions.append("created_at >= ?")
            params.append(since)
        if until:
            conditions.append("created_at <= ?")
            params.append(until)
        if search_data:
            conditions.append("data LIKE ?")
            params.append(f"%{search_data}%")
        if tags:
            # Match records containing any of the specified tags
            for tag in tags:
                conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        limit = min(limit, 200)

        rows = db.execute(
            f"SELECT * FROM analysis_records {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

        return [_row_to_record(dict(row)) for row in rows]
    finally:
        db.close()


def get_record(record_id: str) -> Record | None:
    """Get a single record by UUID."""
    db = get_connection()
    try:
        row = db.execute(
            "SELECT * FROM analysis_records WHERE id = ?", (record_id,)
        ).fetchone()
        return _row_to_record(dict(row)) if row else None
    finally:
        db.close()


def list_analysis_types() -> list[AnalysisTypeSummary]:
    """Get summary of all analysis types stored."""
    db = get_connection()
    try:
        rows = db.execute("""
            SELECT
                analysis_type,
                COUNT(*) as count,
                MAX(created_at) as newest_record,
                MIN(created_at) as oldest_record
            FROM analysis_records
            GROUP BY analysis_type
            ORDER BY count DESC
        """).fetchall()

        return [
            AnalysisTypeSummary(
                analysis_type=row["analysis_type"],
                count=row["count"],
                newest_record=row["newest_record"],
                oldest_record=row["oldest_record"],
            )
            for row in rows
        ]
    finally:
        db.close()


def get_analytics(
    analysis_type: str | None = None,
    since: str | None = None,
) -> AnalyticsSummary:
    """Get summary statistics for stored data."""
    db = get_connection()
    try:
        conditions: list[str] = []
        params: list = []

        if analysis_type:
            conditions.append("analysis_type = ?")
            params.append(analysis_type)
        if since:
            conditions.append("created_at >= ?")
            params.append(since)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        total = db.execute(
            f"SELECT COUNT(*) as cnt FROM analysis_records {where}", params
        ).fetchone()["cnt"]

        types = db.execute(
            f"""
            SELECT analysis_type, COUNT(*) as count,
                   MAX(created_at) as newest_record,
                   MIN(created_at) as oldest_record
            FROM analysis_records {where}
            GROUP BY analysis_type ORDER BY count DESC
            """,
            params,
        ).fetchall()

        top_entities = db.execute(
            f"""
            SELECT entity_id, entity_name, COUNT(*) as count
            FROM analysis_records
            {where}
            {'AND' if where else 'WHERE'} entity_id IS NOT NULL
            GROUP BY entity_id ORDER BY count DESC LIMIT 10
            """,
            params,
        ).fetchall()

        # Get DB file size
        data_dir = os.environ.get("CLAY_DATA_DIR", ".")
        db_path = os.path.join(data_dir, "clay.db")
        size_mb = os.path.getsize(db_path) / (1024 * 1024) if os.path.exists(db_path) else 0

        return AnalyticsSummary(
            total_records=total,
            records_by_type=[
                AnalysisTypeSummary(
                    analysis_type=r["analysis_type"],
                    count=r["count"],
                    newest_record=r["newest_record"],
                    oldest_record=r["oldest_record"],
                )
                for r in types
            ],
            top_entities=[
                {"entity_id": r["entity_id"], "entity_name": r["entity_name"], "count": r["count"]}
                for r in top_entities
            ],
            storage_size_mb=round(size_mb, 2),
        )
    finally:
        db.close()


def delete_records(
    analysis_type: str | None = None,
    older_than: str | None = None,
    record_ids: list[str] | None = None,
) -> DeleteResult:
    """Delete records matching criteria."""
    db = get_connection()
    try:
        conditions: list[str] = []
        params: list = []

        if analysis_type:
            conditions.append("analysis_type = ?")
            params.append(analysis_type)
        if older_than:
            conditions.append("created_at < ?")
            params.append(older_than)
        if record_ids:
            placeholders = ",".join("?" for _ in record_ids)
            conditions.append(f"id IN ({placeholders})")
            params.extend(record_ids)

        if not conditions:
            return DeleteResult(deleted=0)

        where = f"WHERE {' AND '.join(conditions)}"

        # Also delete from vec table if it exists
        try:
            vec_ids = db.execute(
                f"SELECT id FROM analysis_records {where}", params
            ).fetchall()
            if vec_ids:
                id_list = [r["id"] for r in vec_ids]
                placeholders = ",".join("?" for _ in id_list)
                db.execute(
                    f"DELETE FROM vec_records WHERE record_id IN ({placeholders})", id_list
                )
        except Exception:
            pass  # vec table may not exist yet

        cursor = db.execute(
            f"DELETE FROM analysis_records {where}", params
        )
        db.commit()

        return DeleteResult(deleted=cursor.rowcount)
    finally:
        db.close()
