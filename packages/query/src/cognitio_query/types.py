"""Query-side domain and result types."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from cognitio_storage.enums import Freshness, Workflow
from pydantic import BaseModel, Field


class Principal(BaseModel):
    id: UUID
    tenant_id: UUID
    source_identities: dict[str, str] = Field(default_factory=dict)
    group_ids: frozenset[str] = frozenset()


class ResolvedAcl(BaseModel):
    principal_ids: frozenset[str]
    group_ids: frozenset[str]


class EffectiveAcl(BaseModel):
    public: bool = False
    allowed_principals: frozenset[str] = frozenset()
    allowed_groups: frozenset[str] = frozenset()
    denied_principals: frozenset[str] = frozenset()
    denied_groups: frozenset[str] = frozenset()


class SourceSummary(BaseModel):
    id: UUID
    connector: str
    source_id: str
    title: str | None = None
    source_url: str | None = None


class SearchCandidate(BaseModel):
    extraction_id: UUID
    tier: str
    similarity: float
    source: SourceSummary
    effective_acl: EffectiveAcl
    confidence: float | None = None
    freshness: Freshness
    workflow: Workflow
    created_at: datetime


class SearchHit(BaseModel):
    extraction_id: UUID
    tier: str
    similarity: float
    score: float
    source: SourceSummary
    confidence: float | None = None
    freshness: Freshness
    workflow: Workflow
    warning: str | None = None


class ContextBudget(BaseModel):
    max_nodes: int = Field(default=50, ge=1, le=500)
    max_chars: int = Field(default=20_000, ge=1)
    max_depth: int = Field(default=2, ge=0, le=5)
    fan_out_by_edge: dict[str, int] = Field(default_factory=dict)


class ContextNode(BaseModel):
    id: UUID
    node_type: str
    text: str
    source: SourceSummary
    warning: str | None = None


class AssembledContext(BaseModel):
    nodes: tuple[ContextNode, ...]
    truncated: bool
    total_chars: int
