"""SQLAlchemy models — one per table in ARCHITECTURE.md → Layer 1 Schema.

Nothing above Storage defines a table. The declarative ``Base`` and the shared column
conventions live in :mod:`cognitio_storage.types`; the Postgres ENUM value sets live in
:mod:`cognitio_storage.enums`.

Cross-cutting invariants enforced here structurally:

* **tenant scoping** — every table carries a non-null ``tenant_id``; parent→child foreign
  keys are *tenant-safe composite* FKs (``(tenant_id, x) -> (parent.tenant_id, parent.id)``)
  so a row can never reference another tenant's data.
* **one current row** — ``is_current`` uniqueness is a partial unique index, not app logic.
* **crypto-shred** — ``source_versions`` stores the encrypted ``raw_content`` plus its
  per-record ``enc_key_id`` so right-to-deletion destroys the key, not the row.
* **mandatory evidence** — an extraction with an empty ``evidence_spans`` is rejected by a
  CHECK constraint.
* **edge fan-out caps** — ``supports``/``contradicts`` edge counts per node are capped
  (see :data:`MAX_SUPPORTS_PER_NODE` / :data:`MAX_CONTRADICTS_PER_NODE`); enforced at write
  time by the edge repository and a Postgres trigger created in the migration.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from cognitio_storage import enums
from cognitio_storage.types import (
    Base,
    TenantScoped,
    fk_uuid,
    optional_fk_uuid,
    tenant_fk,
    tenant_unique,
    updated_at_column,
)

__all__ = [
    "Base",
    "EMBEDDING_DIM",
    "MAX_CONTRADICTS_PER_NODE",
    "MAX_SUPPORTS_PER_NODE",
    "ChangeEventRow",
    "ConnectorScanItem",
    "ConnectorSyncState",
    "Conflict",
    "Edge",
    "Embedding",
    "Entity",
    "EntityMention",
    "Extraction",
    "NormalizedChunk",
    "NormalizedDocument",
    "Principal",
    "ReviewItem",
    "SourceAclRule",
    "SourceItem",
    "SourceVersion",
    "Tenant",
]

# pgvector dimension for the Phase-1 embedding model (text-embedding-3-small).
EMBEDDING_DIM = 1536

# Edge fan-out caps (DESIGN: prevent contradiction "hairballs" around popular Gold facts).
MAX_SUPPORTS_PER_NODE = 50
MAX_CONTRADICTS_PER_NODE = 20


# --- Postgres ENUM types (one object per type, reused across columns) -------------------------
def _pg_enum(py_enum: type[Any], name: str) -> SAEnum:
    return SAEnum(
        py_enum,
        name=name,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
        create_type=True,
    )


lifecycle_enum = _pg_enum(enums.Lifecycle, "lifecycle_t")
freshness_enum = _pg_enum(enums.Freshness, "freshness_t")
workflow_enum = _pg_enum(enums.Workflow, "workflow_t")
trust_state_enum = _pg_enum(enums.TrustState, "trust_state_t")
gold_source_enum = _pg_enum(enums.GoldSource, "gold_source_t")
node_type_enum = _pg_enum(enums.NodeType, "node_type_t")
entity_type_enum = _pg_enum(enums.EntityType, "entity_type_t")
edge_type_enum = _pg_enum(enums.EdgeType, "edge_type_t")
provenance_enum = _pg_enum(enums.Provenance, "provenance_t")
change_type_enum = _pg_enum(enums.ChangeType, "change_type_t")
job_status_enum = _pg_enum(enums.JobStatus, "job_status_t")

_EMPTY_OBJECT = text("'{}'::jsonb")
_EMPTY_ARRAY = text("'[]'::jsonb")


# =============================================================================================
# Tenant + identity / ACL tables (task 9)
# =============================================================================================
class Tenant(Base):
    """Registry of tenants. ``tenant_id`` on every other table is one of these ids."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (UniqueConstraint("slug"),)


class Principal(TenantScoped):
    """A Cognitio user mapped to its per-source identities (ACL resolution input)."""

    __tablename__ = "principals"

    cognitio_user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    source_identities: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=_EMPTY_ARRAY
    )
    group_memberships_cache: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    cache_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        tenant_unique(),
        UniqueConstraint("tenant_id", "cognitio_user_id"),
    )


class SourceAclRule(TenantScoped):
    """A captured allow/deny ACL rule for a source item (row form of the ``acl`` descriptor)."""

    __tablename__ = "source_acl_rules"

    source_item_id: Mapped[uuid.UUID] = fk_uuid()
    principal_kind: Mapped[str] = mapped_column(Text, nullable=False)  # 'user' | 'group'
    principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    access: Mapped[str] = mapped_column(Text, nullable=False)  # 'allow' | 'deny'

    __table_args__ = (
        tenant_fk("source_item_id", "source_items", ondelete="CASCADE"),
        UniqueConstraint("tenant_id", "source_item_id", "principal_kind", "principal_id", "access"),
        CheckConstraint("principal_kind IN ('user','group')", name="principal_kind_valid"),
        CheckConstraint("access IN ('allow','deny')", name="access_valid"),
        Index("ix_source_acl_item", "tenant_id", "source_item_id"),
    )


# =============================================================================================
# Source, sync, and normalization tables (task 7)
# =============================================================================================
class SourceItem(TenantScoped):
    """A logical external object (a Notion page, a thread)."""

    __tablename__ = "source_items"

    node_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    connector: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Denormalized pointer to the current version; integrity kept by the app (avoids a
    # circular DB FK with source_versions.source_item_id).
    current_version_id: Mapped[uuid.UUID | None] = optional_fk_uuid()
    source_revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    acl: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=_EMPTY_OBJECT)
    lifecycle: Mapped[enums.Lifecycle] = mapped_column(
        lifecycle_enum, nullable=False, server_default=text("'active'")
    )
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (
        tenant_unique(),
        UniqueConstraint("tenant_id", "connector", "source_id"),
        Index("ix_source_items_lifecycle", "tenant_id", "connector", "lifecycle"),
    )


class SourceVersion(TenantScoped):
    """Tier 0 immutable raw snapshot. ``raw_content`` is the encrypted crypto-shred unit."""

    __tablename__ = "source_versions"

    source_item_id: Mapped[uuid.UUID] = fk_uuid()
    content_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    raw_content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    enc_key_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    fetched_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=_EMPTY_OBJECT
    )
    acl_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=_EMPTY_OBJECT
    )
    source_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    is_current: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    __table_args__ = (
        tenant_unique(),
        tenant_fk("source_item_id", "source_items", ondelete="CASCADE"),
        UniqueConstraint("tenant_id", "source_item_id", "content_hash"),
        Index(
            "one_current_version",
            "source_item_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )


class NormalizedDocument(TenantScoped):
    """Tier 1 normalized text; offsets are stable against ``normalized_text``."""

    __tablename__ = "normalized_documents"

    source_version_id: Mapped[uuid.UUID] = fk_uuid()
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_current: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    __table_args__ = (
        tenant_unique(),
        tenant_fk("source_version_id", "source_versions", ondelete="CASCADE"),
        Index(
            "one_current_norm",
            "source_version_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )


class NormalizedChunk(TenantScoped):
    """A stable chunk of a normalized document with document-global offsets + content hash."""

    __tablename__ = "normalized_chunks"

    normalized_document_id: Mapped[uuid.UUID] = fk_uuid()
    chunk_id: Mapped[str] = mapped_column(Text, nullable=False)  # deterministic stable id
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # sha256(text)
    text_content: Mapped[str] = mapped_column("text", Text, nullable=False)

    __table_args__ = (
        tenant_unique(),
        tenant_fk("normalized_document_id", "normalized_documents", ondelete="CASCADE"),
        UniqueConstraint("tenant_id", "normalized_document_id", "chunk_id"),
        CheckConstraint("end_char >= start_char", name="chunk_span_ordered"),
        Index("ix_chunks_doc", "tenant_id", "normalized_document_id", "ordinal"),
        Index("ix_chunks_hash", "tenant_id", "chunk_hash"),
    )


class ChangeEventRow(TenantScoped):
    """Per-source idempotent change record (connector sync state)."""

    __tablename__ = "change_events"

    connector: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    high_watermark: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_type: Mapped[enums.ChangeType] = mapped_column(change_type_enum, nullable=False)
    source_revision: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[enums.JobStatus] = mapped_column(
        job_status_enum, nullable=False, server_default=text("'pending'")
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "connector", "source_id", "source_revision"),
        Index(
            "ix_change_events_pending",
            "tenant_id",
            "connector",
            postgresql_where=text("status = 'pending'"),
        ),
    )


class ConnectorSyncState(TenantScoped):
    """Durable checkpoint/health for a connector: cursor, high-watermark, scan generation."""

    __tablename__ = "connector_sync_states"

    connector: Mapped[str] = mapped_column(Text, nullable=False)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    high_watermark: Mapped[str | None] = mapped_column(Text, nullable=True)
    scan_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    last_successful_reconciliation: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    health: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ok'"))
    dead_letter_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = updated_at_column()

    __table_args__ = (UniqueConstraint("tenant_id", "connector"),)


class ConnectorScanItem(TenantScoped):
    """Membership of a source in a given full-scan generation (for tombstone detection)."""

    __tablename__ = "connector_scan_items"

    connector: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    scan_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "connector", "source_id", "scan_generation"),
        Index("ix_scan_items_generation", "tenant_id", "connector", "scan_generation"),
    )


# =============================================================================================
# Extraction, entity, edge, conflict, review, embedding tables (task 8)
# =============================================================================================
class Extraction(TenantScoped):
    """Tier 2/3 typed extracted record. Evidence is mandatory (CHECK constraint)."""

    __tablename__ = "extractions"

    node_type: Mapped[enums.NodeType] = mapped_column(node_type_enum, nullable=False)
    source_version_id: Mapped[uuid.UUID] = fk_uuid()
    normalized_document_id: Mapped[uuid.UUID] = fk_uuid()
    chunk_id: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Promoted columns (written explicitly by Extraction, not generated from jsonb).
    owner_entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    item_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_spans: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    effective_acl: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=_EMPTY_OBJECT
    )
    trust_state: Mapped[enums.TrustState] = mapped_column(
        trust_state_enum, nullable=False, server_default=text("'extracted'")
    )
    gold_source: Mapped[enums.GoldSource | None] = mapped_column(gold_source_enum, nullable=True)
    lifecycle: Mapped[enums.Lifecycle] = mapped_column(
        lifecycle_enum, nullable=False, server_default=text("'active'")
    )
    freshness: Mapped[enums.Freshness] = mapped_column(
        freshness_enum, nullable=False, server_default=text("'current'")
    )
    workflow: Mapped[enums.Workflow] = mapped_column(
        workflow_enum, nullable=False, server_default=text("'none'")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    is_current: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    __table_args__ = (
        tenant_unique(),
        tenant_fk("source_version_id", "source_versions"),
        tenant_fk("normalized_document_id", "normalized_documents"),
        CheckConstraint("jsonb_array_length(evidence_spans) >= 1", name="evidence_nonempty"),
        CheckConstraint(
            "trust_state <> 'gold' OR gold_source IS NOT NULL", name="gold_needs_source"
        ),
        Index(
            "uniq_extraction_fp",
            "tenant_id",
            "fingerprint",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        Index("ix_extr_owner", "tenant_id", "owner_entity_id", postgresql_where=text("is_current")),
        Index("ix_extr_due", "tenant_id", "due_date", postgresql_where=text("is_current")),
        Index("ix_extr_trust", "tenant_id", "trust_state", postgresql_where=text("is_current")),
        Index("ix_extr_flow", "tenant_id", "workflow", postgresql_where=text("workflow <> 'none'")),
        Index("ix_extr_stale", "tenant_id", postgresql_where=text("freshness = 'stale'")),
        Index("ix_extr_payload", "payload", postgresql_using="gin"),
    )


class EntityMention(TenantScoped):
    """A mention span in source text, resolved to an entity in a later pass."""

    __tablename__ = "entity_mentions"

    extraction_id: Mapped[uuid.UUID | None] = optional_fk_uuid()
    normalized_document_id: Mapped[uuid.UUID] = fk_uuid()
    surface_form: Mapped[str] = mapped_column(Text, nullable=False)
    span: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    resolved_entity_id: Mapped[uuid.UUID | None] = optional_fk_uuid()
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        tenant_fk("extraction_id", "extractions", ondelete="CASCADE"),
        tenant_fk("normalized_document_id", "normalized_documents", ondelete="CASCADE"),
        tenant_fk("resolved_entity_id", "entities"),
        Index(
            "ix_mention_unresolved",
            "tenant_id",
            postgresql_where=text("resolved_entity_id IS NULL"),
        ),
        Index("ix_mention_entity", "tenant_id", "resolved_entity_id"),
    )


class Entity(TenantScoped):
    """Tier 3 canonical entity (post-resolution)."""

    __tablename__ = "entities"

    node_type: Mapped[enums.EntityType] = mapped_column(entity_type_enum, nullable=False)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=_EMPTY_ARRAY)
    attributes: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=_EMPTY_ARRAY
    )
    lifecycle: Mapped[enums.Lifecycle] = mapped_column(
        lifecycle_enum, nullable=False, server_default=text("'active'")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    is_current: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    __table_args__ = (
        tenant_unique(),
        Index(
            "ix_entity_name",
            "tenant_id",
            text("lower(canonical_name)"),
            postgresql_where=text("is_current"),
        ),
    )


class Edge(TenantScoped):
    """Typed relationship between any two nodes. No FKs by design (spans every node type).

    ``supports``/``contradicts`` fan-out is capped per source node; the cap is enforced by
    the edge repository and a Postgres trigger (see the migration).
    """

    __tablename__ = "edges"

    from_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    from_type: Mapped[str] = mapped_column(Text, nullable=False)
    to_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    to_type: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[enums.EdgeType] = mapped_column(edge_type_enum, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    provenance: Mapped[enums.Provenance] = mapped_column(provenance_enum, nullable=False)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    evidence_spans: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_edge_from", "tenant_id", "from_id", "type"),
        Index("ix_edge_to", "tenant_id", "to_id", "type"),
    )


class Conflict(TenantScoped):
    """A first-class contradiction resolution unit (Phase 2 lifecycle)."""

    __tablename__ = "conflicts"

    member_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    contradicts_edge_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    detector_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    proposed_resolution: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'"))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_conflict_open", "tenant_id", postgresql_where=text("status = 'open'")),
    )


class ReviewItem(TenantScoped):
    """Review workflow + audit trail for an extracted record."""

    __tablename__ = "review_items"

    target_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    workflow: Mapped[enums.Workflow] = mapped_column(workflow_enum, nullable=False)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    cost_attributed: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "ix_review_open",
            "tenant_id",
            "created_at",
            postgresql_where=text("decided_at IS NULL"),
        ),
    )


class Embedding(TenantScoped):
    """Version-aware embedding vector. Queries pin one ``model_version`` (one HNSW space)."""

    __tablename__ = "embeddings"

    object_type: Mapped[str] = mapped_column(Text, nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    vector: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)

    __table_args__ = (UniqueConstraint("tenant_id", "object_type", "object_id", "model_version"),)
