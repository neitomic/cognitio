"""Integration tests for the storage repositories (task 11).

These run against a migrated Postgres (the ``db_session`` fixture binds a session to a
connection-level transaction that is always rolled back, so nothing survives the test). They
prove the tenant-scoped write/read path, idempotency, the monotonic revision guard, edge
fan-out caps, and cross-tenant isolation.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from cognitio_storage.enums import ChangeType, EdgeType, NodeType, Provenance
from cognitio_storage.models import EMBEDDING_DIM
from cognitio_storage.repositories import (
    ChangeEventRepository,
    ChunkInput,
    ConnectorScanRepository,
    ConnectorSyncStateRepository,
    EdgeCapExceeded,
    EdgeRepository,
    EmbeddingRepository,
    ExtractionRepository,
    NormalizedChunkRepository,
    NormalizedDocumentRepository,
    ReviewItemRepository,
    SourceItemRepository,
    SourceVersionRepository,
    TenantRepository,
)
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


def _tid() -> uuid.UUID:
    return uuid.uuid4()


async def test_full_source_to_extraction_chain(db_session: AsyncSession) -> None:
    tenant = _tid()
    items = SourceItemRepository(db_session)
    versions = SourceVersionRepository(db_session)
    docs = NormalizedDocumentRepository(db_session)
    chunks = NormalizedChunkRepository(db_session)
    extractions = ExtractionRepository(db_session)

    item, created = await items.upsert_ref(tenant, "notion", "page-1", node_type="page")
    assert created is True

    raw = b"# Title\n\nThe team decided to ship on Friday."
    version, vcreated = await versions.insert_if_new(
        tenant,
        item.id,
        content_hash=hashlib.sha256(raw).digest(),
        raw_content=raw,
        acl_snapshot={"public": False},
    )
    assert vcreated is True
    await items.set_current_version(tenant, item.id, version.id)

    text = "The team decided to ship on Friday."
    doc = await docs.insert(tenant, version.id, text)
    chunk_hash = hashlib.sha256(text.encode()).digest()
    await chunks.insert_many(
        tenant,
        doc.id,
        [ChunkInput("chunk-0", 0, 0, len(text), chunk_hash, text)],
    )

    fp = hashlib.sha256(b"fact|ship friday|0:35").digest()
    extraction, ecreated = await extractions.insert_if_absent(
        tenant,
        node_type=NodeType.FACT,
        source_version_id=version.id,
        normalized_document_id=doc.id,
        chunk_id="chunk-0",
        payload={"claim": text},
        evidence_spans=[{"start_char": 0, "end_char": len(text), "text": text}],
        fingerprint=fp,
        confidence=0.9,
    )
    assert ecreated is True

    # read back the whole chain, tenant-scoped
    assert (await items.get(tenant, item.id)) is not None
    assert (await versions.get_current(tenant, item.id)).id == version.id  # type: ignore[union-attr]
    assert (await docs.get_current(tenant, version.id)).id == doc.id  # type: ignore[union-attr]
    persisted = await chunks.list_for_document(tenant, doc.id)
    assert [c.chunk_id for c in persisted] == ["chunk-0"]
    assert (await extractions.get(tenant, extraction.id)).chunk_id == "chunk-0"  # type: ignore[union-attr]


async def test_upsert_ref_is_idempotent(db_session: AsyncSession) -> None:
    tenant = _tid()
    items = SourceItemRepository(db_session)
    first, c1 = await items.upsert_ref(tenant, "notion", "page-1")
    second, c2 = await items.upsert_ref(tenant, "notion", "page-1")
    assert c1 is True and c2 is False
    assert first.id == second.id


async def test_source_version_dedup_and_current_pointer(db_session: AsyncSession) -> None:
    tenant = _tid()
    items = SourceItemRepository(db_session)
    versions = SourceVersionRepository(db_session)
    item, _ = await items.upsert_ref(tenant, "notion", "page-1")

    raw_a = b"v1"
    v1, c1 = await versions.insert_if_new(
        tenant, item.id, content_hash=hashlib.sha256(raw_a).digest(), raw_content=raw_a
    )
    # same content hash -> no-op
    v1_again, c1b = await versions.insert_if_new(
        tenant, item.id, content_hash=hashlib.sha256(raw_a).digest(), raw_content=raw_a
    )
    assert c1 is True and c1b is False
    assert v1.id == v1_again.id

    raw_b = b"v2"
    v2, c2 = await versions.insert_if_new(
        tenant, item.id, content_hash=hashlib.sha256(raw_b).digest(), raw_content=raw_b
    )
    assert c2 is True
    # the new snapshot is the single current version
    current = await versions.get_current(tenant, item.id)
    assert current is not None and current.id == v2.id


async def test_revision_is_monotonic(db_session: AsyncSession) -> None:
    tenant = _tid()
    items = SourceItemRepository(db_session)
    item, _ = await items.upsert_ref(tenant, "notion", "page-1")

    assert await items.bump_revision(tenant, item.id, 5) is True
    # regression is a no-op
    assert await items.bump_revision(tenant, item.id, 3) is False
    refreshed = await items.get(tenant, item.id)
    assert refreshed is not None and refreshed.source_revision == 5
    # advancing still works
    assert await items.bump_revision(tenant, item.id, 6) is True


async def test_extraction_fingerprint_idempotent(db_session: AsyncSession) -> None:
    tenant = _tid()
    item, _ = await SourceItemRepository(db_session).upsert_ref(tenant, "notion", "p")
    raw = b"x"
    version, _ = await SourceVersionRepository(db_session).insert_if_new(
        tenant, item.id, content_hash=hashlib.sha256(raw).digest(), raw_content=raw
    )
    doc = await NormalizedDocumentRepository(db_session).insert(tenant, version.id, "hello")
    repo = ExtractionRepository(db_session)
    fp = hashlib.sha256(b"fp").digest()
    kwargs = dict(
        node_type=NodeType.FACT,
        source_version_id=version.id,
        normalized_document_id=doc.id,
        chunk_id="c0",
        payload={"k": "v"},
        evidence_spans=[{"start_char": 0, "end_char": 5, "text": "hello"}],
        fingerprint=fp,
    )
    _, c1 = await repo.insert_if_absent(tenant, **kwargs)  # type: ignore[arg-type]
    _, c2 = await repo.insert_if_absent(tenant, **kwargs)  # type: ignore[arg-type]
    assert c1 is True and c2 is False


async def test_cross_tenant_reads_are_isolated(db_session: AsyncSession) -> None:
    tenant_a, tenant_b = _tid(), _tid()
    items = SourceItemRepository(db_session)
    item_a, _ = await items.upsert_ref(tenant_a, "notion", "page-1")
    # tenant B cannot read tenant A's item by id
    assert await items.get(tenant_b, item_a.id) is None
    # and the same external source_id in tenant B is a distinct row
    item_b, created = await items.upsert_ref(tenant_b, "notion", "page-1")
    assert created is True and item_b.id != item_a.id


async def test_edge_fanout_cap_guard(db_session: AsyncSession) -> None:
    tenant = _tid()
    repo = EdgeRepository(db_session)
    gold = _tid()
    # contradicts cap is 20: 20 succeed, the 21st raises
    for _ in range(20):
        await repo.insert(
            tenant,
            from_id=gold,
            from_type="extraction",
            to_id=_tid(),
            to_type="extraction",
            edge_type=EdgeType.CONTRADICTS,
            provenance=Provenance.MODEL,
            confidence=0.85,
        )
    assert await repo.count_by_type(tenant, gold, EdgeType.CONTRADICTS) == 20
    with pytest.raises(EdgeCapExceeded):
        await repo.insert(
            tenant,
            from_id=gold,
            from_type="extraction",
            to_id=_tid(),
            to_type="extraction",
            edge_type=EdgeType.CONTRADICTS,
            provenance=Provenance.MODEL,
        )


async def test_sync_state_checkpoint_advances_on_empty(db_session: AsyncSession) -> None:
    tenant = _tid()
    repo = ConnectorSyncStateRepository(db_session)
    state = await repo.checkpoint(tenant, "notion", cursor=None, high_watermark="hwm-1")
    assert state.high_watermark == "hwm-1"
    # an empty scan still records progress (new high-watermark, cursor cleared)
    state = await repo.checkpoint(tenant, "notion", cursor=None, high_watermark="hwm-2")
    assert state.high_watermark == "hwm-2"
    gen = await repo.advance_generation(tenant, "notion")
    assert gen == 1


async def test_scan_membership_detects_missing(db_session: AsyncSession) -> None:
    tenant = _tid()
    repo = ConnectorScanRepository(db_session)
    # generation 1 saw pages 1,2,3
    for sid in ("p1", "p2", "p3"):
        assert await repo.record_seen(tenant, "notion", sid, 1) is True
    # idempotent
    assert await repo.record_seen(tenant, "notion", "p1", 1) is False
    # generation 2 saw only 1,3 -> p2 is a tombstone candidate
    for sid in ("p1", "p3"):
        await repo.record_seen(tenant, "notion", sid, 2)
    missing = await repo.missing_since(tenant, "notion", prior_generation=1, current_generation=2)
    assert list(missing) == ["p2"]


async def test_embedding_upsert_is_idempotent(db_session: AsyncSession) -> None:
    tenant = _tid()
    repo = EmbeddingRepository(db_session)
    obj = _tid()
    vec = [0.0] * EMBEDDING_DIM
    await repo.upsert(
        tenant,
        object_type="extraction",
        object_id=obj,
        model="text-embedding-3-small",
        model_version="text-embedding-3-small/1",
        vector=vec,
    )
    vec2 = [1.0] + [0.0] * (EMBEDDING_DIM - 1)
    await repo.upsert(
        tenant,
        object_type="extraction",
        object_id=obj,
        model="text-embedding-3-small",
        model_version="text-embedding-3-small/1",
        vector=vec2,
    )
    got = await repo.get(
        tenant,
        object_type="extraction",
        object_id=obj,
        model_version="text-embedding-3-small/1",
    )
    assert got is not None
    assert list(got.vector)[0] == pytest.approx(1.0)


async def test_change_events_idempotent(db_session: AsyncSession) -> None:
    tenant = _tid()
    repo = ChangeEventRepository(db_session)
    _, c1 = await repo.insert_if_new(
        tenant, "notion", "p1", change_type=ChangeType.UPDATED, source_revision=1
    )
    _, c2 = await repo.insert_if_new(
        tenant, "notion", "p1", change_type=ChangeType.UPDATED, source_revision=1
    )
    assert c1 is True and c2 is False
    pending = await repo.list_pending(tenant, "notion")
    assert len(pending) == 1


async def test_tenant_and_review_repositories(db_session: AsyncSession) -> None:
    tenants = TenantRepository(db_session)
    tenant = await tenants.create("Acme", "acme")
    assert (await tenants.get_by_slug("acme")).id == tenant.id  # type: ignore[union-attr]

    reviews = ReviewItemRepository(db_session)
    item = await reviews.open(tenant.id, target_id=_tid(), target_type="extraction")
    assert item.decided_at is None
    open_items = await reviews.list_open(tenant.id)
    assert [i.id for i in open_items] == [item.id]
