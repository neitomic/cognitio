"""Normalized document + chunk repositories (Tier 1 write path)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cognitio_storage.models import NormalizedChunk, NormalizedDocument


@dataclass(frozen=True)
class ChunkInput:
    """A stable chunk to persist: deterministic id, document-global offsets, content hash."""

    chunk_id: str
    ordinal: int
    start_char: int
    end_char: int
    chunk_hash: bytes
    text: str


class NormalizedDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def insert(
        self,
        tenant_id: uuid.UUID,
        source_version_id: uuid.UUID,
        normalized_text: str,
        *,
        language: str | None = None,
        make_current: bool = True,
    ) -> NormalizedDocument:
        if make_current:
            await self._demote_current(tenant_id, source_version_id)
        doc = NormalizedDocument(
            tenant_id=tenant_id,
            source_version_id=source_version_id,
            normalized_text=normalized_text,
            language=language,
            is_current=make_current,
        )
        self._s.add(doc)
        await self._s.flush()
        return doc

    async def _demote_current(self, tenant_id: uuid.UUID, source_version_id: uuid.UUID) -> None:
        stmt = (
            update(NormalizedDocument)
            .where(
                NormalizedDocument.tenant_id == tenant_id,
                NormalizedDocument.source_version_id == source_version_id,
                NormalizedDocument.is_current.is_(True),
            )
            .values(is_current=False)
        )
        await self._s.execute(stmt)

    async def get(self, tenant_id: uuid.UUID, doc_id: uuid.UUID) -> NormalizedDocument | None:
        stmt = select(NormalizedDocument).where(
            NormalizedDocument.tenant_id == tenant_id, NormalizedDocument.id == doc_id
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def get_current(
        self, tenant_id: uuid.UUID, source_version_id: uuid.UUID
    ) -> NormalizedDocument | None:
        stmt = select(NormalizedDocument).where(
            NormalizedDocument.tenant_id == tenant_id,
            NormalizedDocument.source_version_id == source_version_id,
            NormalizedDocument.is_current.is_(True),
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()


class NormalizedChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def insert_many(
        self,
        tenant_id: uuid.UUID,
        normalized_document_id: uuid.UUID,
        chunks: Sequence[ChunkInput],
    ) -> list[NormalizedChunk]:
        rows = [
            NormalizedChunk(
                tenant_id=tenant_id,
                normalized_document_id=normalized_document_id,
                chunk_id=c.chunk_id,
                ordinal=c.ordinal,
                start_char=c.start_char,
                end_char=c.end_char,
                chunk_hash=c.chunk_hash,
                text_content=c.text,
            )
            for c in chunks
        ]
        self._s.add_all(rows)
        await self._s.flush()
        return rows

    async def list_for_document(
        self, tenant_id: uuid.UUID, normalized_document_id: uuid.UUID
    ) -> Sequence[NormalizedChunk]:
        stmt = (
            select(NormalizedChunk)
            .where(
                NormalizedChunk.tenant_id == tenant_id,
                NormalizedChunk.normalized_document_id == normalized_document_id,
            )
            .order_by(NormalizedChunk.ordinal)
        )
        return (await self._s.execute(stmt)).scalars().all()

    async def hashes_for_document(
        self, tenant_id: uuid.UUID, normalized_document_id: uuid.UUID
    ) -> dict[str, bytes]:
        """Map chunk_id -> chunk_hash, for diffing against a freshly chunked document."""
        rows = await self.list_for_document(tenant_id, normalized_document_id)
        return {row.chunk_id: row.chunk_hash for row in rows}
