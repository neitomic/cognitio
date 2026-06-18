"""Enum value sets, mirrored from the Postgres ENUM types.

These mirror the `CREATE TYPE ... AS ENUM` declarations in ARCHITECTURE.md → Layer 1.
Keep this file and the migration that creates the Postgres ENUMs in lock-step: the
string values here MUST equal the Postgres enum labels exactly.
"""

from __future__ import annotations

from enum import StrEnum


class Lifecycle(StrEnum):
    """Is this record part of the live knowledge base?"""

    ACTIVE = "active"
    ARCHIVED = "archived"


class Freshness(StrEnum):
    """Does this reflect the latest source version, or is it queued for re-derivation?"""

    CURRENT = "current"
    STALE = "stale"


class Workflow(StrEnum):
    """Where is this record in the human / conflict workflow?"""

    NONE = "none"
    PENDING_REVIEW = "pending_review"
    DISPUTED = "disputed"


class TrustState(StrEnum):
    """The Gold ladder. First-class, indexable state on `extractions`."""

    EXTRACTED = "extracted"
    GOLD = "gold"
    SUPERSEDED = "superseded"


class GoldSource(StrEnum):
    """How a record became Gold. Required whenever trust_state == GOLD."""

    HUMAN_REVIEW = "human_review"
    AUTHORITATIVE_SOURCE = "authoritative_source"
    AUTO_PROMOTED = "auto_promoted"  # Phase 2+ only; see ADR 0005.


class NodeType(StrEnum):
    """Extraction record kinds."""

    DECISION = "decision"
    ACTION = "action"
    FACT = "fact"
    ENTITY_REF = "entity_ref"
    OPEN_QUESTION = "open_question"


class EntityType(StrEnum):
    """Canonical entity kinds."""

    PERSON = "person"
    TEAM = "team"
    PRODUCT = "product"
    SYSTEM = "system"
    CUSTOMER = "customer"
    VENDOR = "vendor"
    PROJECT = "project"
    REPOSITORY = "repository"
    DOCUMENT = "document"
    METRIC = "metric"
    OTHER = "other"


class EdgeType(StrEnum):
    """Typed relationship kinds. NB: `related_to` is computed, never stored (ADR 0003)."""

    DERIVED_FROM = "derived_from"
    REFERENCES = "references"
    SUPERSEDES = "supersedes"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"


class Provenance(StrEnum):
    """Who/what created an edge."""

    HUMAN = "human"
    MODEL = "model"
    VECTOR = "vector"
    PARSER = "parser"


class ChangeType(StrEnum):
    """Connector-reported change kinds (permission_changed is content-invisible)."""

    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    PERMISSION_CHANGED = "permission_changed"


class JobStatus(StrEnum):
    """Queue + change-event processing states."""

    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
