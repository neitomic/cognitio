"""Validated job envelopes crossing the queue boundary."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class JobType(StrEnum):
    FETCH = "fetch"
    NORMALIZE = "normalize"
    CHUNK = "chunk"
    EMBED = "embed"
    EXTRACT = "extract"
    ENTITY_RESOLVE = "entity_resolve"
    INVALIDATE = "invalidate"


class FetchPayload(BaseModel):
    type: Literal[JobType.FETCH] = JobType.FETCH
    source_item_id: UUID


class NormalizePayload(BaseModel):
    type: Literal[JobType.NORMALIZE] = JobType.NORMALIZE
    source_version_id: UUID


class ChunkPayload(BaseModel):
    type: Literal[JobType.CHUNK] = JobType.CHUNK
    normalized_document_id: UUID


class EmbedPayload(BaseModel):
    type: Literal[JobType.EMBED] = JobType.EMBED
    object_type: str
    object_id: UUID
    chunk_id: str
    model_version: str


class ExtractPayload(BaseModel):
    type: Literal[JobType.EXTRACT] = JobType.EXTRACT
    normalized_document_id: UUID
    chunk_id: str
    stale_extraction_id: UUID | None = None


class EntityResolvePayload(BaseModel):
    type: Literal[JobType.ENTITY_RESOLVE] = JobType.ENTITY_RESOLVE
    mention_ids: tuple[UUID, ...]


class InvalidatePayload(BaseModel):
    type: Literal[JobType.INVALIDATE] = JobType.INVALIDATE
    source_version_id: UUID
    changed_chunk_ids: tuple[str, ...]


type JobPayload = Annotated[
    FetchPayload
    | NormalizePayload
    | ChunkPayload
    | EmbedPayload
    | ExtractPayload
    | EntityResolvePayload
    | InvalidatePayload,
    Field(discriminator="type"),
]


class Job(BaseModel):
    id: UUID
    tenant_id: UUID
    type: JobType
    payload: JobPayload
    attempts: int = 0
    priority: int = 100
    run_after: datetime
    locked_by: str | None = None


class NewJob(BaseModel):
    tenant_id: UUID
    type: JobType
    payload: JobPayload
    dedupe_key: str | None = None
    priority: int = 100
    run_after: datetime | None = None
