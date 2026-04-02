"""Pydantic models for records and tool parameters."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RecordInput(BaseModel):
    """A single record to ingest."""

    record_id: str = Field(..., description="Unique identifier from Clay")
    analysis_type: str = Field(..., description="Type of analysis, e.g. 'call_analysis'")
    data: dict = Field(..., description="The analysis payload (flexible JSON)")
    source: str | None = Field(None, description="Data source, e.g. 'gong', 'hubspot'")
    entity_id: str | None = Field(None, description="Shared ID for cross-record joins")
    entity_name: str | None = Field(None, description="Human-readable entity label")
    tags: list[str] = Field(default_factory=list, description="Grouping tags")


class Record(BaseModel):
    """A stored analysis record."""

    id: str
    record_id: str
    analysis_type: str
    data: dict
    source: str | None = None
    entity_id: str | None = None
    entity_name: str | None = None
    tags: list[str] = Field(default_factory=list)
    embedding_model: str | None = None
    created_at: str
    received_at: str


class IngestResult(BaseModel):
    """Result of an ingest operation."""

    ingested: int = 0
    updated: int = 0
    errors: list[str] = Field(default_factory=list)


class AnalysisTypeSummary(BaseModel):
    """Summary of a stored analysis type."""

    analysis_type: str
    count: int
    newest_record: str
    oldest_record: str


class AnalyticsSummary(BaseModel):
    """Overall analytics for stored data."""

    total_records: int
    records_by_type: list[AnalysisTypeSummary]
    top_entities: list[dict]
    storage_size_mb: float


class DeleteResult(BaseModel):
    """Result of a delete operation."""

    deleted: int
