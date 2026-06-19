"""Source item / version / change-event repositories (Tier 0 sync write path).

All statements are scoped by ``tenant_id``. Idempotent writers (``upsert_ref``,
``insert_if_new``) return ``(row, created)`` so callers can tell a fresh write from a no-op.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cognitio_storage.enums import ChangeType, Lifecycle
from cognitio_storage.models import ChangeEventRow, SourceItem, SourceVersion


class SourceItemRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert_ref(
        self,
        tenant_id: uuid.UUID,
        connector: str,
        source_id: str,
        *,
        node_type: str | None = None,
        source_url: str | None = None,
    ) -> tuple[SourceItem, bool]:
        """Insert a source item if absent; idempotent on (tenant, connector, source_id)."""
        existing = await self.get_by_source(tenant_id, connector, source_id)
        if existing is not None:
            return existing, False
        item = SourceItem(
            tenant_id=tenant_id,
            connector=connector,
            source_id=source_id,
            node_type=node_type,
            source_url=source_url,
        )
        self._s.add(item)
        await self._s.flush()
        return item, True

    async def get(self, tenant_id: uuid.UUID, item_id: uuid.UUID) -> SourceItem | None:
        stmt = select(SourceItem).where(SourceItem.tenant_id == tenant_id, SourceItem.id == item_id)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def get_by_source(
        self, tenant_id: uuid.UUID, connector: str, source_id: str
    ) -> SourceItem | None:
        stmt = select(SourceItem).where(
            SourceItem.tenant_id == tenant_id,
            SourceItem.connector == connector,
            SourceItem.source_id == source_id,
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def bump_revision(
        self, tenant_id: uuid.UUID, item_id: uuid.UUID, new_revision: int
    ) -> bool:
        """Monotonic guard: only advance ``source_revision``; no-op (False) on regression."""
        stmt = (
            update(SourceItem)
            .where(
                SourceItem.tenant_id == tenant_id,
                SourceItem.id == item_id,
                SourceItem.source_revision < new_revision,
            )
            .values(source_revision=new_revision)
        )
        result = cast("CursorResult[Any]", await self._s.execute(stmt))
        return (result.rowcount or 0) > 0

    async def set_current_version(
        self, tenant_id: uuid.UUID, item_id: uuid.UUID, version_id: uuid.UUID
    ) -> None:
        stmt = (
            update(SourceItem)
            .where(SourceItem.tenant_id == tenant_id, SourceItem.id == item_id)
            .values(current_version_id=version_id)
        )
        await self._s.execute(stmt)

    async def archive(self, tenant_id: uuid.UUID, item_id: uuid.UUID) -> None:
        stmt = (
            update(SourceItem)
            .where(SourceItem.tenant_id == tenant_id, SourceItem.id == item_id)
            .values(lifecycle=Lifecycle.ARCHIVED)
        )
        await self._s.execute(stmt)


class SourceVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def insert_if_new(
        self,
        tenant_id: uuid.UUID,
        source_item_id: uuid.UUID,
        *,
        content_hash: bytes,
        raw_content: bytes,
        acl_snapshot: dict[str, Any] | None = None,
        fetched_metadata: dict[str, Any] | None = None,
        source_timestamp: datetime | None = None,
        enc_key_id: uuid.UUID | None = None,
        make_current: bool = True,
    ) -> tuple[SourceVersion, bool]:
        """Insert an immutable snapshot; idempotent on (tenant, item, content_hash).

        A genuinely new snapshot becomes the single current version (the previous current
        row is demoted first to satisfy the ``one_current_version`` partial unique index).
        """
        stmt = select(SourceVersion).where(
            SourceVersion.tenant_id == tenant_id,
            SourceVersion.source_item_id == source_item_id,
            SourceVersion.content_hash == content_hash,
        )
        existing = (await self._s.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing, False

        if make_current:
            await self._demote_current(tenant_id, source_item_id)
        version = SourceVersion(
            tenant_id=tenant_id,
            source_item_id=source_item_id,
            content_hash=content_hash,
            raw_content=raw_content,
            acl_snapshot=acl_snapshot or {},
            fetched_metadata=fetched_metadata or {},
            source_timestamp=source_timestamp,
            enc_key_id=enc_key_id,
            is_current=make_current,
        )
        self._s.add(version)
        await self._s.flush()
        return version, True

    async def _demote_current(self, tenant_id: uuid.UUID, source_item_id: uuid.UUID) -> None:
        stmt = (
            update(SourceVersion)
            .where(
                SourceVersion.tenant_id == tenant_id,
                SourceVersion.source_item_id == source_item_id,
                SourceVersion.is_current.is_(True),
            )
            .values(is_current=False)
        )
        await self._s.execute(stmt)

    async def get_current(
        self, tenant_id: uuid.UUID, source_item_id: uuid.UUID
    ) -> SourceVersion | None:
        stmt = select(SourceVersion).where(
            SourceVersion.tenant_id == tenant_id,
            SourceVersion.source_item_id == source_item_id,
            SourceVersion.is_current.is_(True),
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()


class ChangeEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def insert_if_new(
        self,
        tenant_id: uuid.UUID,
        connector: str,
        source_id: str,
        *,
        change_type: ChangeType,
        source_revision: int | None,
        cursor: str | None = None,
        high_watermark: str | None = None,
    ) -> tuple[ChangeEventRow, bool]:
        """Idempotent on (tenant, connector, source_id, source_revision)."""
        stmt = select(ChangeEventRow).where(
            ChangeEventRow.tenant_id == tenant_id,
            ChangeEventRow.connector == connector,
            ChangeEventRow.source_id == source_id,
            ChangeEventRow.source_revision == source_revision,
        )
        existing = (await self._s.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing, False
        event = ChangeEventRow(
            tenant_id=tenant_id,
            connector=connector,
            source_id=source_id,
            change_type=change_type,
            source_revision=source_revision,
            cursor=cursor,
            high_watermark=high_watermark,
        )
        self._s.add(event)
        await self._s.flush()
        return event, True

    async def list_pending(self, tenant_id: uuid.UUID, connector: str) -> Sequence[ChangeEventRow]:
        from cognitio_storage.enums import JobStatus

        stmt = select(ChangeEventRow).where(
            ChangeEventRow.tenant_id == tenant_id,
            ChangeEventRow.connector == connector,
            ChangeEventRow.status == JobStatus.PENDING,
        )
        return (await self._s.execute(stmt)).scalars().all()
