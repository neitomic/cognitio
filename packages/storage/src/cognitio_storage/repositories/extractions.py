"""Extraction repository (Tier 2/3 typed records).

Idempotency is keyed on the current-row fingerprint unique index
(``uniq_extraction_fp`` over ``(tenant_id, fingerprint) WHERE is_current``).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cognitio_storage.enums import Freshness, GoldSource, NodeType, TrustState
from cognitio_storage.models import Extraction


class ExtractionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def insert_if_absent(
        self,
        tenant_id: uuid.UUID,
        *,
        node_type: NodeType,
        source_version_id: uuid.UUID,
        normalized_document_id: uuid.UUID,
        chunk_id: str,
        payload: dict[str, Any],
        evidence_spans: list[dict[str, Any]],
        fingerprint: bytes,
        confidence: float | None = None,
        effective_acl: dict[str, Any] | None = None,
        owner_entity_id: uuid.UUID | None = None,
        claim_type: str | None = None,
    ) -> tuple[Extraction, bool]:
        """Insert unless a current row with the same fingerprint exists in this tenant."""
        existing = await self.current_by_fingerprint(tenant_id, fingerprint)
        if existing is not None:
            return existing, False
        row = Extraction(
            tenant_id=tenant_id,
            node_type=node_type,
            source_version_id=source_version_id,
            normalized_document_id=normalized_document_id,
            chunk_id=chunk_id,
            payload=payload,
            evidence_spans=evidence_spans,
            fingerprint=fingerprint,
            confidence=confidence,
            effective_acl=effective_acl or {},
            owner_entity_id=owner_entity_id,
            claim_type=claim_type,
        )
        self._s.add(row)
        await self._s.flush()
        return row, True

    async def get(self, tenant_id: uuid.UUID, extraction_id: uuid.UUID) -> Extraction | None:
        stmt = select(Extraction).where(
            Extraction.tenant_id == tenant_id, Extraction.id == extraction_id
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def current_by_fingerprint(
        self, tenant_id: uuid.UUID, fingerprint: bytes
    ) -> Extraction | None:
        stmt = select(Extraction).where(
            Extraction.tenant_id == tenant_id,
            Extraction.fingerprint == fingerprint,
            Extraction.is_current.is_(True),
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def by_chunk(
        self, tenant_id: uuid.UUID, chunk_ids: Sequence[str], *, current: bool = True
    ) -> Sequence[Extraction]:
        stmt = select(Extraction).where(
            Extraction.tenant_id == tenant_id,
            Extraction.chunk_id.in_(list(chunk_ids)),
        )
        if current:
            stmt = stmt.where(Extraction.is_current.is_(True))
        return (await self._s.execute(stmt)).scalars().all()

    async def mark_stale(self, tenant_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> int:
        """Flag records stale per-record; returns the number flagged."""
        if not ids:
            return 0
        stmt = (
            update(Extraction)
            .where(Extraction.tenant_id == tenant_id, Extraction.id.in_(list(ids)))
            .values(freshness=Freshness.STALE)
        )
        result = cast("CursorResult[Any]", await self._s.execute(stmt))
        return result.rowcount or 0

    async def set_trust(
        self,
        tenant_id: uuid.UUID,
        extraction_id: uuid.UUID,
        trust_state: TrustState,
        gold_source: GoldSource | None = None,
    ) -> None:
        stmt = (
            update(Extraction)
            .where(Extraction.tenant_id == tenant_id, Extraction.id == extraction_id)
            .values(trust_state=trust_state, gold_source=gold_source)
        )
        await self._s.execute(stmt)
