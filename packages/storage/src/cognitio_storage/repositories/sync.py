"""Connector sync-state + scan-membership repositories.

``ConnectorSyncStateRepository`` is the durable checkpoint store: cursor, high-watermark,
scan generation, and health. ``checkpoint`` advances the high-watermark **even on an empty
scan** so a long quiet period still records progress (ARCHITECTURE → Connector gotchas).
``ConnectorScanRepository`` records per-generation membership so a completed full scan can
detect sources that disappeared (tombstones).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cognitio_storage.models import ConnectorScanItem, ConnectorSyncState


class ConnectorSyncStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def load(self, tenant_id: uuid.UUID, connector: str) -> ConnectorSyncState | None:
        stmt = select(ConnectorSyncState).where(
            ConnectorSyncState.tenant_id == tenant_id,
            ConnectorSyncState.connector == connector,
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def get_or_create(self, tenant_id: uuid.UUID, connector: str) -> ConnectorSyncState:
        state = await self.load(tenant_id, connector)
        if state is not None:
            return state
        state = ConnectorSyncState(tenant_id=tenant_id, connector=connector)
        self._s.add(state)
        await self._s.flush()
        return state

    async def checkpoint(
        self,
        tenant_id: uuid.UUID,
        connector: str,
        *,
        cursor: str | None,
        high_watermark: str | None,
    ) -> ConnectorSyncState:
        """Persist the cursor + high-watermark. Advances even when a scan yielded no items."""
        state = await self.get_or_create(tenant_id, connector)
        state.cursor = cursor
        if high_watermark is not None:
            state.high_watermark = high_watermark
        state.last_attempted_at = datetime.now(UTC)
        await self._s.flush()
        return state

    async def mark_success(
        self, tenant_id: uuid.UUID, connector: str, *, when: datetime | None = None
    ) -> None:
        stmt = (
            update(ConnectorSyncState)
            .where(
                ConnectorSyncState.tenant_id == tenant_id,
                ConnectorSyncState.connector == connector,
            )
            .values(
                last_successful_reconciliation=when or datetime.now(UTC),
                health="ok",
                error=None,
            )
        )
        await self._s.execute(stmt)

    async def record_error(self, tenant_id: uuid.UUID, connector: str, error: str) -> None:
        state = await self.get_or_create(tenant_id, connector)
        state.health = "error"
        state.error = error
        state.dead_letter_count += 1
        await self._s.flush()

    async def advance_generation(self, tenant_id: uuid.UUID, connector: str) -> int:
        """Bump and return the scan generation for a new full scan."""
        state = await self.get_or_create(tenant_id, connector)
        state.scan_generation += 1
        await self._s.flush()
        return state.scan_generation


class ConnectorScanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record_seen(
        self,
        tenant_id: uuid.UUID,
        connector: str,
        source_id: str,
        scan_generation: int,
    ) -> bool:
        """Record membership in a scan generation; idempotent. True if newly recorded."""
        stmt = select(ConnectorScanItem).where(
            ConnectorScanItem.tenant_id == tenant_id,
            ConnectorScanItem.connector == connector,
            ConnectorScanItem.source_id == source_id,
            ConnectorScanItem.scan_generation == scan_generation,
        )
        if (await self._s.execute(stmt)).scalar_one_or_none() is not None:
            return False
        self._s.add(
            ConnectorScanItem(
                tenant_id=tenant_id,
                connector=connector,
                source_id=source_id,
                scan_generation=scan_generation,
            )
        )
        await self._s.flush()
        return True

    async def source_ids_for_generation(
        self, tenant_id: uuid.UUID, connector: str, scan_generation: int
    ) -> set[str]:
        stmt = select(ConnectorScanItem.source_id).where(
            ConnectorScanItem.tenant_id == tenant_id,
            ConnectorScanItem.connector == connector,
            ConnectorScanItem.scan_generation == scan_generation,
        )
        return set((await self._s.execute(stmt)).scalars().all())

    async def missing_since(
        self,
        tenant_id: uuid.UUID,
        connector: str,
        *,
        prior_generation: int,
        current_generation: int,
    ) -> Sequence[str]:
        """Source ids present in the prior completed scan but absent from the current one.

        These are tombstone candidates — only meaningful after a *successful* full scan.
        """
        prior = await self.source_ids_for_generation(tenant_id, connector, prior_generation)
        current = await self.source_ids_for_generation(tenant_id, connector, current_generation)
        return sorted(prior - current)
