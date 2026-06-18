"""Review and conflict domain types."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from cognitio_storage.enums import NodeType, Workflow
from pydantic import BaseModel, Field


class ReviewDecision(StrEnum):
    CONFIRM = "confirm"
    EDIT = "edit"
    REJECT = "reject"


class ReviewDisposition(StrEnum):
    SOFT_REVIEW = "soft_review"
    HARD_GATE = "hard_gate"
    AUTO_PROMOTE = "auto_promote"


class ReviewFilters(BaseModel):
    workflow: Workflow | None = None
    node_type: NodeType | None = None
    source_item_id: UUID | None = None
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = None


class ReviewItem(BaseModel):
    id: UUID
    tenant_id: UUID
    target_id: UUID
    target_type: str
    workflow: Workflow
    reviewer_id: UUID | None = None
    decision: ReviewDecision | None = None
    before: dict[str, object] | None = None
    after: dict[str, object] | None = None
    cost_attributed: Decimal | None = None
    created_at: datetime
    decided_at: datetime | None = None


class ReviewPage(BaseModel):
    items: tuple[ReviewItem, ...]
    next_cursor: str | None = None


class ConflictStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class Conflict(BaseModel):
    id: UUID
    tenant_id: UUID
    member_ids: tuple[UUID, ...]
    contradicts_edge_ids: tuple[UUID, ...]
    detector_confidence: float | None = None
    proposed_resolution: dict[str, object] | None = None
    status: ConflictStatus
    resolved_by: UUID | None = None
    resolved_at: datetime | None = None
