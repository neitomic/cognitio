"""Phase 2 conflict detection and whole-set resolution contracts."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from cognitio_review.types import Conflict


class ConflictService(Protocol):
    async def detect(self, tenant_id: UUID, extraction_id: UUID) -> tuple[Conflict, ...]:
        # TODO(Phase 2): candidate generation plus separately-scored contradiction classifier.
        ...

    async def resolve(
        self,
        tenant_id: UUID,
        conflict_id: UUID,
        resolution: dict[str, object],
        reviewer_id: UUID,
    ) -> Conflict:
        """Resolve and clear workflow state for the complete conflict set atomically."""
        ...
