"""Review lifecycle service contract.

The storage implementation must apply the audit row and extraction state transition atomically.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from cognitio_review.types import ReviewDecision, ReviewFilters, ReviewItem, ReviewPage


class ReviewRepository(Protocol):
    async def list(self, tenant_id: UUID, filters: ReviewFilters) -> ReviewPage: ...

    async def get(self, tenant_id: UUID, item_id: UUID) -> ReviewItem | None: ...

    async def apply_decision(
        self,
        tenant_id: UUID,
        item_id: UUID,
        decision: ReviewDecision,
        reviewer_id: UUID,
        edit: dict[str, object] | None,
    ) -> ReviewItem: ...


class ReviewService:
    def __init__(self, repository: ReviewRepository) -> None:
        self._repository = repository

    async def queue(self, tenant_id: UUID, filters: ReviewFilters) -> ReviewPage:
        return await self._repository.list(tenant_id, filters)

    async def get(self, tenant_id: UUID, item_id: UUID) -> ReviewItem | None:
        return await self._repository.get(tenant_id, item_id)

    async def decide(
        self,
        tenant_id: UUID,
        item_id: UUID,
        decision: ReviewDecision,
        reviewer_id: UUID,
        edit: dict[str, object] | None = None,
    ) -> ReviewItem:
        if decision is ReviewDecision.EDIT and edit is None:
            raise ValueError("An edit payload is required for the edit decision")
        if decision is not ReviewDecision.EDIT and edit is not None:
            raise ValueError("Edit payload is only valid for the edit decision")
        return await self._repository.apply_decision(
            tenant_id, item_id, decision, reviewer_id, edit
        )
