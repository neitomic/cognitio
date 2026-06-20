"""Sync cursor persistence contract and connector retry policy.

The :class:`SyncCursorStore` protocol is the abstract persistence boundary; the concrete
:class:`DbSyncCursorStore` backs it with the ``connector_sync_states`` table through the
Storage repositories. ``checkpoint`` advances the high-watermark even when a scan yielded no
items, so a long quiet period still records progress.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from cognitio_storage.repositories import ConnectorSyncStateRepository
from sqlalchemy.ext.asyncio import AsyncSession

from cognitio_connectors.base import SyncCheckpoint


class SyncCursorStore(Protocol):
    async def load(self, tenant_id: UUID, connector: str) -> str | None: ...

    async def checkpoint(
        self,
        tenant_id: UUID,
        connector: str,
        cursor: str | None,
        high_watermark: str | None,
    ) -> None: ...


class DbSyncCursorStore:
    """Concrete :class:`SyncCursorStore` backed by ``connector_sync_states``.

    Takes the caller's session (so a scan's checkpoint commits in the same transaction as the
    page of change events it just persisted).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = ConnectorSyncStateRepository(session)

    async def load(self, tenant_id: UUID, connector: str) -> str | None:
        state = await self._repo.load(tenant_id, connector)
        return state.cursor if state is not None else None

    async def load_checkpoint(self, tenant_id: UUID, connector: str) -> SyncCheckpoint:
        """Load the full resumable position (cursor + high-watermark + scan generation)."""
        state = await self._repo.load(tenant_id, connector)
        if state is None:
            return SyncCheckpoint(cursor=None, high_watermark=None, scan_generation=0)
        return SyncCheckpoint(
            cursor=state.cursor,
            high_watermark=state.high_watermark,
            scan_generation=state.scan_generation,
        )

    async def checkpoint(
        self,
        tenant_id: UUID,
        connector: str,
        cursor: str | None,
        high_watermark: str | None,
    ) -> None:
        await self._repo.checkpoint(
            tenant_id, connector, cursor=cursor, high_watermark=high_watermark
        )


@dataclass(frozen=True)
class RetryPolicy:
    base_seconds: float = 2.0
    max_seconds: float = 300.0
    max_attempts: int = 5
    jitter_ratio: float = 0.2

    def next_delay(self, attempts: int) -> float:
        exponent = max(attempts - 1, 0)
        delay = min(self.max_seconds, self.base_seconds * (2**exponent))
        jitter = delay * self.jitter_ratio
        return float(max(0.0, delay + random.uniform(-jitter, jitter)))

    def is_dead_letter(self, attempts: int) -> bool:
        return attempts >= self.max_attempts
