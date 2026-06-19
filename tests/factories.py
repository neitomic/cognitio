"""Fixture factories for Cognitio domain objects.

These build *valid* in-memory value objects from the real package types so unit tests can
exercise behaviour without a database or provider credentials. The same builders back the
pytest fixtures in ``conftest.py``; they are importable directly (``from factories import
make_job``) thanks to ``pythonpath = ["tests"]`` in ``pyproject.toml``.

Each builder is deterministic given its arguments and accepts overrides via keyword so a test
can vary exactly the field under test.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from cognitio_connectors.base import (
    AccessDescriptor,
    SourceRef,
    SourceSnapshot,
)
from cognitio_extraction.client import NormalizedDocument
from cognitio_extraction.prompt import Chunk, DocContextHeader
from cognitio_extraction.schema import (
    Certainty,
    ClaimType,
    EvidenceSpan,
    ExtractionEnvelope,
    ExtractionSource,
    FactQualifiers,
    FactRecord,
)
from cognitio_pipeline.types import FetchPayload, Job, JobPayload, JobType, NewJob
from cognitio_storage.enums import ChangeType

_EPOCH = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def make_tenant_id() -> UUID:
    """A fresh tenant id. Every row in Cognitio is tenant-scoped."""
    return uuid4()


def make_access_descriptor(
    *,
    public: bool = False,
    allowed_principals: tuple[str, ...] = ("principal-alice",),
    allowed_groups: tuple[str, ...] = (),
    denied_principals: tuple[str, ...] = (),
    denied_groups: tuple[str, ...] = (),
) -> AccessDescriptor:
    """A captured source ACL (the connector-side access descriptor)."""
    return AccessDescriptor(
        public=public,
        allowed_principals=frozenset(allowed_principals),
        allowed_groups=frozenset(allowed_groups),
        denied_principals=frozenset(denied_principals),
        denied_groups=frozenset(denied_groups),
    )


def make_source_ref(
    *,
    source_id: str = "page-1",
    object_type: str = "page",
    source_url: str | None = "https://notion.so/page-1",
    parent_id: str | None = None,
) -> SourceRef:
    return SourceRef(
        source_id=source_id,
        object_type=object_type,
        source_url=source_url,
        parent_id=parent_id,
    )


def make_source_snapshot(
    *,
    source_id: str = "page-1",
    raw_content: bytes = b"# Title\n\nThe team decided to ship on Friday.",
    acl: AccessDescriptor | None = None,
    source_timestamp: datetime | None = _EPOCH,
    source_revision: int = 1,
    metadata: dict[str, object] | None = None,
) -> SourceSnapshot:
    """An immutable source snapshot with a content hash derived from ``raw_content``."""
    return SourceSnapshot(
        source_id=source_id,
        raw_content=raw_content,
        content_hash=hashlib.sha256(raw_content).digest(),
        acl=acl if acl is not None else make_access_descriptor(),
        source_timestamp=source_timestamp,
        source_revision=source_revision,
        metadata=metadata if metadata is not None else {"title": "Title"},
    )


def make_normalized_document(
    *,
    source_version_id: UUID | None = None,
    normalized_text: str = "The team decided to ship on Friday.",
    doc_id: UUID | None = None,
) -> NormalizedDocument:
    return NormalizedDocument(
        id=doc_id or uuid4(),
        source_version_id=source_version_id or uuid4(),
        normalized_text=normalized_text,
    )


def make_chunk(
    *,
    chunk_id: str = "chunk-0",
    text: str = "The team decided to ship on Friday.",
    start_char: int = 0,
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        start_char=start_char,
        end_char=start_char + len(text),
        text=text,
    )


def make_doc_context(
    *,
    connector: str = "notion",
    source_id: str = "page-1",
    source_version_id: UUID | None = None,
    title: str = "Title",
) -> DocContextHeader:
    return DocContextHeader(
        connector=connector,
        source_id=source_id,
        source_version_id=source_version_id or uuid4(),
        title=title,
        source_timestamp=_EPOCH,
        language="en",
    )


def make_extraction_envelope(
    *,
    connector: str = "notion",
    source_id: str = "page-1",
    source_version_id: UUID | None = None,
    chunk_id: str = "chunk-0",
    title: str = "Title",
    claim: str = "The team decided to ship on Friday.",
) -> ExtractionEnvelope:
    """A minimal but schema-valid extraction envelope carrying one evidence-backed fact."""
    return ExtractionEnvelope(
        source=ExtractionSource(
            connector=connector,
            source_id=source_id,
            source_version_id=source_version_id or uuid4(),
            chunk_id=chunk_id,
            title=title,
        ),
        facts=(
            FactRecord(
                local_id="f1",
                evidence_spans=(EvidenceSpan(start_char=0, end_char=len(claim), text=claim),),
                confidence=0.9,
                claim=claim,
                claim_type=ClaimType.STATE,
                subject_entities=(),
                qualifiers=FactQualifiers(certainty=Certainty.CERTAIN),
            ),
        ),
    )


def make_change_type(value: str = "updated") -> ChangeType:
    return ChangeType(value)


def make_fetch_payload(*, source_item_id: UUID | None = None) -> FetchPayload:
    return FetchPayload(source_item_id=source_item_id or uuid4())


def make_job(
    *,
    tenant_id: UUID | None = None,
    payload: JobPayload | None = None,
    attempts: int = 0,
    run_after: datetime | None = None,
) -> Job:
    resolved_payload = payload if payload is not None else make_fetch_payload()
    return Job(
        id=uuid4(),
        tenant_id=tenant_id or make_tenant_id(),
        type=JobType(resolved_payload.type),
        payload=resolved_payload,
        attempts=attempts,
        run_after=run_after or _EPOCH,
    )


def make_new_job(
    *,
    tenant_id: UUID | None = None,
    payload: JobPayload | None = None,
    dedupe_key: str | None = None,
) -> NewJob:
    resolved_payload = payload if payload is not None else make_fetch_payload()
    return NewJob(
        tenant_id=tenant_id or make_tenant_id(),
        type=JobType(resolved_payload.type),
        payload=resolved_payload,
        dedupe_key=dedupe_key,
    )
