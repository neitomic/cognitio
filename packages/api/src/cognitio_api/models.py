"""API-only request and response models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from cognitio_connectors.base import ConnectorHealth
from cognitio_extraction.schema import EvidenceSpan
from cognitio_review.types import ReviewItem
from pydantic import BaseModel


class ReconcileResponse(BaseModel):
    job_id: UUID


class ReviewEditRequest(BaseModel):
    payload: dict[str, object]


class ReviewItemDetail(BaseModel):
    item: ReviewItem
    extraction: dict[str, object]
    evidence: tuple[EvidenceSpan, ...]
    normalized_text: str
    source: dict[str, object]
    provenance: tuple[dict[str, object], ...] = ()


class SourceItemDetail(BaseModel):
    id: UUID
    connector: str
    source_id: str
    source_url: str | None
    lifecycle: str
    updated_at: datetime
    versions: tuple[dict[str, object], ...] = ()
    extractions: tuple[dict[str, object], ...] = ()


class ConnectorList(BaseModel):
    items: tuple[ConnectorHealth, ...]
