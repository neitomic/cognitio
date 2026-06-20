"""Schema metadata tests (tasks 7-9).

These assert — without a database — that every required column, unique key, revision /
current-row guard, chunk offset/hash, crypto-shred unit, fan-out-relevant column, tenant
predicate, and index declared by ARCHITECTURE.md exists on the SQLAlchemy models. They are
the cheap first line of defence before the migration is exercised against Postgres.
"""

from __future__ import annotations

from cognitio_storage.models import (
    MAX_CONTRADICTS_PER_NODE,
    MAX_SUPPORTS_PER_NODE,
    Base,
)
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Table, UniqueConstraint

TABLES: dict[str, Table] = Base.metadata.tables

ALL_TABLES = {
    # task 9
    "tenants",
    "principals",
    "source_acl_rules",
    # task 7
    "source_items",
    "source_versions",
    "normalized_documents",
    "normalized_chunks",
    "change_events",
    "connector_sync_states",
    "connector_scan_items",
    # task 8
    "extractions",
    "entity_mentions",
    "entities",
    "edges",
    "conflicts",
    "review_items",
    "embeddings",
}


def _columns(table_name: str) -> set[str]:
    return set(TABLES[table_name].columns.keys())


def _index_names(table_name: str) -> set[str]:
    return {ix.name for ix in TABLES[table_name].indexes if ix.name}


def _unique_column_sets(table_name: str) -> list[frozenset[str]]:
    sets: list[frozenset[str]] = []
    for c in TABLES[table_name].constraints:
        if isinstance(c, UniqueConstraint):
            sets.append(frozenset(col.name for col in c.columns))
    for ix in TABLES[table_name].indexes:
        if ix.unique:
            sets.append(frozenset(col.name for col in ix.columns))
    return sets


def _check_names(table_name: str) -> set[str]:
    """Check-constraint names with the ``ck_<table>_`` naming-convention prefix stripped."""
    names: set[str] = set()
    prefix = f"ck_{table_name}_"
    for c in TABLES[table_name].constraints:
        if isinstance(c, CheckConstraint) and c.name:
            names.add(c.name.removeprefix(prefix))
    return names


def _fk_pairs(table_name: str) -> set[tuple[frozenset[str], str]]:
    """(local column names, referred table) for each (composite) FK constraint."""
    pairs: set[tuple[frozenset[str], str]] = set()
    for c in TABLES[table_name].constraints:
        if isinstance(c, ForeignKeyConstraint):
            local = frozenset(elem.parent.name for elem in c.elements)
            referred = c.elements[0].column.table.name
            pairs.add((local, referred))
    return pairs


# --------------------------------------------------------------------------------------------
def test_all_tables_present() -> None:
    assert ALL_TABLES <= set(TABLES)


def test_every_table_is_tenant_scoped() -> None:
    """Every table except the tenant registry itself carries a non-null tenant_id."""
    for name in ALL_TABLES:
        if name == "tenants":
            assert "id" in _columns(name)
            continue
        cols = TABLES[name].columns
        assert "tenant_id" in cols, f"{name} missing tenant_id"
        assert cols["tenant_id"].nullable is False, f"{name}.tenant_id must be NOT NULL"
        assert "id" in cols and "created_at" in cols


# --- task 9 ----------------------------------------------------------------------------------
def test_tenants_table() -> None:
    assert {"id", "name", "slug", "created_at"} <= _columns("tenants")
    assert frozenset({"slug"}) in _unique_column_sets("tenants")


def test_principals_table() -> None:
    assert {"cognitio_user_id", "source_identities", "group_memberships_cache"} <= _columns(
        "principals"
    )
    assert frozenset({"tenant_id", "cognitio_user_id"}) in _unique_column_sets("principals")


def test_source_acl_rules_table() -> None:
    cols = _columns("source_acl_rules")
    assert {"source_item_id", "principal_kind", "principal_id", "access"} <= cols
    assert (frozenset({"tenant_id", "source_item_id"}), "source_items") in _fk_pairs(
        "source_acl_rules"
    )


# --- task 7 ----------------------------------------------------------------------------------
def test_source_items_columns_and_unique() -> None:
    cols = _columns("source_items")
    assert {"connector", "source_id", "source_revision", "acl", "lifecycle", "updated_at"} <= cols
    assert frozenset({"tenant_id", "connector", "source_id"}) in _unique_column_sets("source_items")


def test_source_versions_crypto_shred_and_current() -> None:
    cols = _columns("source_versions")
    # crypto-shred unit: encrypted raw_content + per-record key reference
    assert {"raw_content", "enc_key_id", "content_hash", "acl_snapshot"} <= cols
    assert frozenset({"tenant_id", "source_item_id", "content_hash"}) in _unique_column_sets(
        "source_versions"
    )
    assert "one_current_version" in _index_names("source_versions")
    # tenant-safe composite FK to source_items
    assert (frozenset({"tenant_id", "source_item_id"}), "source_items") in _fk_pairs(
        "source_versions"
    )


def test_normalized_documents_current_guard() -> None:
    assert {"source_version_id", "normalized_text", "language"} <= _columns("normalized_documents")
    assert "one_current_norm" in _index_names("normalized_documents")


def test_normalized_chunks_offsets_and_hash() -> None:
    cols = _columns("normalized_chunks")
    assert {"chunk_id", "ordinal", "start_char", "end_char", "chunk_hash"} <= cols
    assert frozenset({"tenant_id", "normalized_document_id", "chunk_id"}) in _unique_column_sets(
        "normalized_chunks"
    )
    assert "chunk_span_ordered" in _check_names("normalized_chunks")


def test_change_events_idempotent_key() -> None:
    cols = _columns("change_events")
    assert {"connector", "source_id", "cursor", "high_watermark", "change_type", "status"} <= cols
    assert frozenset(
        {"tenant_id", "connector", "source_id", "source_revision"}
    ) in _unique_column_sets("change_events")


def test_connector_sync_states_health() -> None:
    cols = _columns("connector_sync_states")
    assert {
        "connector",
        "cursor",
        "high_watermark",
        "scan_generation",
        "last_successful_reconciliation",
        "dead_letter_count",
        "health",
    } <= cols
    assert frozenset({"tenant_id", "connector"}) in _unique_column_sets("connector_sync_states")


def test_connector_scan_items_generation() -> None:
    cols = _columns("connector_scan_items")
    assert {"connector", "source_id", "scan_generation"} <= cols
    assert frozenset(
        {"tenant_id", "connector", "source_id", "scan_generation"}
    ) in _unique_column_sets("connector_scan_items")


# --- task 8 ----------------------------------------------------------------------------------
def test_extractions_evidence_fingerprint_and_indexes() -> None:
    cols = _columns("extractions")
    assert {
        "node_type",
        "source_version_id",
        "normalized_document_id",
        "chunk_id",
        "payload",
        "evidence_spans",
        "fingerprint",
        "confidence",
        "effective_acl",
        "trust_state",
        "gold_source",
        "freshness",
        "workflow",
        "is_current",
    } <= cols
    checks = _check_names("extractions")
    assert "evidence_nonempty" in checks
    assert "gold_needs_source" in checks
    # current-only fingerprint uniqueness is a partial unique index
    assert frozenset({"tenant_id", "fingerprint"}) in _unique_column_sets("extractions")
    idx = _index_names("extractions")
    assert {"uniq_extraction_fp", "ix_extr_trust", "ix_extr_stale", "ix_extr_payload"} <= idx
    # tenant-safe FKs to both the version and the normalized document
    fks = _fk_pairs("extractions")
    assert (frozenset({"tenant_id", "source_version_id"}), "source_versions") in fks
    assert (frozenset({"tenant_id", "normalized_document_id"}), "normalized_documents") in fks


def test_entity_mentions_resolution_indexes() -> None:
    cols = _columns("entity_mentions")
    assert {"surface_form", "span", "resolved_entity_id", "extraction_id"} <= cols
    assert "ix_mention_unresolved" in _index_names("entity_mentions")


def test_entities_name_index() -> None:
    assert {"node_type", "canonical_name", "aliases", "attributes", "is_current"} <= _columns(
        "entities"
    )
    assert "ix_entity_name" in _index_names("entities")


def test_edges_have_no_foreign_keys_but_lookup_indexes() -> None:
    # edges span every node type -> NO FKs by design (ADR / ARCHITECTURE)
    assert _fk_pairs("edges") == set()
    assert {"ix_edge_from", "ix_edge_to"} <= _index_names("edges")
    assert {"from_id", "from_type", "to_id", "to_type", "type", "confidence", "provenance"} <= (
        _columns("edges")
    )
    # fan-out caps are declared as module constants enforced at write time
    assert MAX_SUPPORTS_PER_NODE == 50
    assert MAX_CONTRADICTS_PER_NODE == 20


def test_conflicts_open_index() -> None:
    assert {"member_ids", "contradicts_edge_ids", "status"} <= _columns("conflicts")
    assert "ix_conflict_open" in _index_names("conflicts")


def test_review_items_audit_fields_and_open_index() -> None:
    cols = _columns("review_items")
    assert {"target_id", "target_type", "workflow", "decision", "before", "after"} <= cols
    assert "ix_review_open" in _index_names("review_items")


def test_embeddings_one_per_object_version() -> None:
    cols = _columns("embeddings")
    assert {"object_type", "object_id", "model", "model_version", "vector"} <= cols
    assert frozenset(
        {"tenant_id", "object_type", "object_id", "model_version"}
    ) in _unique_column_sets("embeddings")
