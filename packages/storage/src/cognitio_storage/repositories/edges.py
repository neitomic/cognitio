"""Edge repository with fan-out cap enforcement.

``supports``/``contradicts`` edges grow "hairballs" around popular Gold facts, so each source
node is capped (:data:`~cognitio_storage.models.MAX_SUPPORTS_PER_NODE` /
:data:`~cognitio_storage.models.MAX_CONTRADICTS_PER_NODE`). The cap is enforced here at write
time *and* by a Postgres trigger (defence in depth); the application guard gives a typed error
and lets a caller replace the weakest edge transactionally instead of failing.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cognitio_storage.enums import EdgeType, Provenance
from cognitio_storage.models import (
    MAX_CONTRADICTS_PER_NODE,
    MAX_SUPPORTS_PER_NODE,
    Edge,
)

_FANOUT_CAPS: dict[EdgeType, int] = {
    EdgeType.SUPPORTS: MAX_SUPPORTS_PER_NODE,
    EdgeType.CONTRADICTS: MAX_CONTRADICTS_PER_NODE,
}


class EdgeCapExceeded(Exception):
    """Raised when an edge insert would exceed the per-node fan-out cap for its type."""

    def __init__(self, from_id: uuid.UUID, edge_type: EdgeType, cap: int) -> None:
        self.from_id = from_id
        self.edge_type = edge_type
        self.cap = cap
        super().__init__(f"{edge_type.value} fan-out cap ({cap}) exceeded for node {from_id}")


class EdgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def count_by_type(
        self, tenant_id: uuid.UUID, from_id: uuid.UUID, edge_type: EdgeType
    ) -> int:
        stmt = select(func.count()).where(
            Edge.tenant_id == tenant_id,
            Edge.from_id == from_id,
            Edge.type == edge_type,
        )
        return int((await self._s.execute(stmt)).scalar_one())

    async def insert(
        self,
        tenant_id: uuid.UUID,
        *,
        from_id: uuid.UUID,
        from_type: str,
        to_id: uuid.UUID,
        to_type: str,
        edge_type: EdgeType,
        provenance: Provenance,
        confidence: float | None = None,
        reviewer_id: uuid.UUID | None = None,
    ) -> Edge:
        """Insert an edge, enforcing the fan-out cap for capped types before writing."""
        cap = _FANOUT_CAPS.get(edge_type)
        if cap is not None:
            current = await self.count_by_type(tenant_id, from_id, edge_type)
            if current >= cap:
                raise EdgeCapExceeded(from_id, edge_type, cap)
        edge = Edge(
            tenant_id=tenant_id,
            from_id=from_id,
            from_type=from_type,
            to_id=to_id,
            to_type=to_type,
            type=edge_type,
            provenance=provenance,
            confidence=confidence,
            reviewer_id=reviewer_id,
        )
        self._s.add(edge)
        await self._s.flush()
        return edge

    async def list_from(
        self, tenant_id: uuid.UUID, from_id: uuid.UUID, edge_type: EdgeType | None = None
    ) -> Sequence[Edge]:
        stmt = select(Edge).where(Edge.tenant_id == tenant_id, Edge.from_id == from_id)
        if edge_type is not None:
            stmt = stmt.where(Edge.type == edge_type)
        return (await self._s.execute(stmt)).scalars().all()
