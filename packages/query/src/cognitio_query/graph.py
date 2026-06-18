"""Bounded typed graph traversal contract for Phase 3 GAG."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from cognitio_query.types import AssembledContext, ContextBudget, Principal


class ContextAssembler(Protocol):
    async def assemble(
        self,
        principal: Principal,
        seeds: tuple[UUID, ...],
        budget: ContextBudget,
    ) -> AssembledContext:
        """ACL-filter every expansion; compute related-to only at depth one."""
        # TODO(Phase 3): recursive CTE traversal with edge-specific fan-out caps.
        ...
