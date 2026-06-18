"""Lower-layer service contracts used by HTTP routes."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from cognitio_connectors.base import ConnectorHealth
from cognitio_query.search import SearchService
from cognitio_query.types import Principal
from cognitio_review.queue import ReviewService

from cognitio_api.models import ReviewItemDetail, SourceItemDetail


class SyncService(Protocol):
    async def list_health(self, tenant_id: UUID) -> tuple[ConnectorHealth, ...]: ...

    async def health(self, tenant_id: UUID, connector: str) -> ConnectorHealth | None: ...

    async def reconcile(self, tenant_id: UUID, connector: str) -> UUID: ...


class SourceService(Protocol):
    async def get(self, principal: Principal, source_id: UUID) -> SourceItemDetail | None: ...

    async def versions(
        self, principal: Principal, source_id: UUID
    ) -> tuple[dict[str, object], ...]: ...

    async def extraction(
        self, principal: Principal, extraction_id: UUID
    ) -> dict[str, object] | None: ...


class ReviewDetailService(Protocol):
    async def get(self, principal: Principal, item_id: UUID) -> ReviewItemDetail | None: ...


__all__ = ["ReviewDetailService", "ReviewService", "SearchService", "SourceService", "SyncService"]
