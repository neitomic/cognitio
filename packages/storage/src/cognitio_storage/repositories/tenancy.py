"""Tenant, principal, entity, review-item, and embedding repositories."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from cognitio_storage.enums import EntityType, Workflow
from cognitio_storage.models import Embedding, Entity, Principal, ReviewItem, Tenant


class TenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, name: str, slug: str) -> Tenant:
        tenant = Tenant(name=name, slug=slug)
        self._s.add(tenant)
        await self._s.flush()
        return tenant

    async def get(self, tenant_id: uuid.UUID) -> Tenant | None:
        return (
            await self._s.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Tenant | None:
        return (
            await self._s.execute(select(Tenant).where(Tenant.slug == slug))
        ).scalar_one_or_none()


class PrincipalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert(
        self,
        tenant_id: uuid.UUID,
        cognitio_user_id: uuid.UUID,
        *,
        source_identities: list[dict[str, Any]] | None = None,
    ) -> Principal:
        existing = await self.get(tenant_id, cognitio_user_id)
        if existing is not None:
            if source_identities is not None:
                existing.source_identities = source_identities
            await self._s.flush()
            return existing
        principal = Principal(
            tenant_id=tenant_id,
            cognitio_user_id=cognitio_user_id,
            source_identities=source_identities or [],
        )
        self._s.add(principal)
        await self._s.flush()
        return principal

    async def get(self, tenant_id: uuid.UUID, cognitio_user_id: uuid.UUID) -> Principal | None:
        stmt = select(Principal).where(
            Principal.tenant_id == tenant_id,
            Principal.cognitio_user_id == cognitio_user_id,
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()


class EntityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def insert(
        self,
        tenant_id: uuid.UUID,
        *,
        node_type: EntityType,
        canonical_name: str,
        aliases: list[str] | None = None,
    ) -> Entity:
        entity = Entity(
            tenant_id=tenant_id,
            node_type=node_type,
            canonical_name=canonical_name,
            aliases=aliases or [],
        )
        self._s.add(entity)
        await self._s.flush()
        return entity

    async def get(self, tenant_id: uuid.UUID, entity_id: uuid.UUID) -> Entity | None:
        stmt = select(Entity).where(Entity.tenant_id == tenant_id, Entity.id == entity_id)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def find_current_by_name(
        self, tenant_id: uuid.UUID, canonical_name: str
    ) -> Sequence[Entity]:
        from sqlalchemy import func

        stmt = select(Entity).where(
            Entity.tenant_id == tenant_id,
            func.lower(Entity.canonical_name) == canonical_name.lower(),
            Entity.is_current.is_(True),
        )
        return (await self._s.execute(stmt)).scalars().all()


class ReviewItemRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def open(
        self,
        tenant_id: uuid.UUID,
        *,
        target_id: uuid.UUID,
        target_type: str,
        workflow: Workflow = Workflow.PENDING_REVIEW,
    ) -> ReviewItem:
        item = ReviewItem(
            tenant_id=tenant_id,
            target_id=target_id,
            target_type=target_type,
            workflow=workflow,
        )
        self._s.add(item)
        await self._s.flush()
        return item

    async def get(self, tenant_id: uuid.UUID, item_id: uuid.UUID) -> ReviewItem | None:
        stmt = select(ReviewItem).where(ReviewItem.tenant_id == tenant_id, ReviewItem.id == item_id)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def list_open(self, tenant_id: uuid.UUID) -> Sequence[ReviewItem]:
        stmt = (
            select(ReviewItem)
            .where(ReviewItem.tenant_id == tenant_id, ReviewItem.decided_at.is_(None))
            .order_by(ReviewItem.created_at)
        )
        return (await self._s.execute(stmt)).scalars().all()


class EmbeddingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert(
        self,
        tenant_id: uuid.UUID,
        *,
        object_type: str,
        object_id: uuid.UUID,
        model: str,
        model_version: str,
        vector: Sequence[float],
    ) -> None:
        """Insert or replace the vector for (tenant, object_type, object_id, model_version)."""
        stmt = pg_insert(Embedding).values(
            tenant_id=tenant_id,
            object_type=object_type,
            object_id=object_id,
            model=model,
            model_version=model_version,
            vector=list(vector),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "object_type", "object_id", "model_version"],
            set_={"vector": stmt.excluded.vector, "model": stmt.excluded.model},
        )
        await self._s.execute(stmt)

    async def get(
        self,
        tenant_id: uuid.UUID,
        *,
        object_type: str,
        object_id: uuid.UUID,
        model_version: str,
    ) -> Embedding | None:
        stmt = select(Embedding).where(
            Embedding.tenant_id == tenant_id,
            Embedding.object_type == object_type,
            Embedding.object_id == object_id,
            Embedding.model_version == model_version,
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()
