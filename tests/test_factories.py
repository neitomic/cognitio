"""Unit tests proving the fixture factories build valid domain objects."""

from __future__ import annotations

import hashlib

from cognitio_connectors.base import AccessDescriptor, SourceSnapshot
from cognitio_extraction.schema import ExtractionEnvelope
from cognitio_pipeline.types import Job, NewJob


def test_tenant_ids_are_unique() -> None:
    import factories

    assert factories.make_tenant_id() != factories.make_tenant_id()


def test_source_snapshot_hash_matches_content() -> None:
    import factories

    snapshot = factories.make_source_snapshot(raw_content=b"hello world")
    assert isinstance(snapshot, SourceSnapshot)
    assert snapshot.content_hash == hashlib.sha256(b"hello world").digest()
    assert isinstance(snapshot.acl, AccessDescriptor)


def test_extraction_envelope_is_schema_valid(make_extraction_envelope) -> None:  # type: ignore[no-untyped-def]
    envelope = make_extraction_envelope()
    assert isinstance(envelope, ExtractionEnvelope)
    # Round-trips through the strict schema (extra="forbid", local-id validation, etc.).
    reparsed = ExtractionEnvelope.model_validate(envelope.model_dump())
    assert reparsed.facts[0].claim == envelope.facts[0].claim


def test_chunk_offsets_match_text(make_chunk) -> None:  # type: ignore[no-untyped-def]
    chunk = make_chunk(text="abcdef", start_char=10)
    assert chunk.start_char == 10
    assert chunk.end_char == 16


def test_job_and_new_job_round_trip(make_job, make_new_job) -> None:  # type: ignore[no-untyped-def]
    job = make_job()
    assert isinstance(job, Job)
    assert Job.model_validate(job.model_dump()).payload.type == job.payload.type

    new_job = make_new_job(dedupe_key="k1")
    assert isinstance(new_job, NewJob)
    assert new_job.dedupe_key == "k1"
    assert new_job.type == new_job.payload.type
