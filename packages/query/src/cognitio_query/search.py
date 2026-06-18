"""Model-version-pinned semantic search with ACL filtering before ranking."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from cognitio_storage.enums import Freshness, Workflow

from cognitio_query.acl import AclResolver
from cognitio_query.types import Principal, SearchCandidate, SearchHit


class QueryEmbedder(Protocol):
    async def embed(self, query: str, *, model_version: str) -> tuple[float, ...]: ...


class SemanticSearchRepository(Protocol):
    async def candidates(
        self,
        tenant_id: UUID,
        vector: tuple[float, ...],
        *,
        model_version: str,
        similarity_floor: float,
        candidate_limit: int,
    ) -> tuple[SearchCandidate, ...]:
        """Use pgvector HNSW and pin the requested model version."""
        ...


class SearchService:
    def __init__(
        self,
        repository: SemanticSearchRepository,
        embedder: QueryEmbedder,
        acl: AclResolver,
    ) -> None:
        self._repository = repository
        self._embedder = embedder
        self._acl = acl

    async def search(
        self,
        principal: Principal,
        query: str,
        *,
        model_version: str,
        similarity_floor: float = 0.75,
        limit: int = 20,
    ) -> tuple[SearchHit, ...]:
        if not query.strip():
            raise ValueError("query must not be empty")
        if not model_version.strip():
            raise ValueError("model_version must not be empty")
        if not 0.0 <= similarity_floor <= 1.0:
            raise ValueError("similarity_floor must be between 0 and 1")
        if limit < 1:
            raise ValueError("limit must be positive")
        vector = await self._embedder.embed(query, model_version=model_version)
        candidates = await self._repository.candidates(
            principal.tenant_id,
            vector,
            model_version=model_version,
            similarity_floor=similarity_floor,
            candidate_limit=max(limit * 5, limit),
        )
        resolved = await self._acl.effective_principals(principal)
        visible = (
            candidate
            for candidate in candidates
            if self._acl.allows(candidate.effective_acl, resolved)
        )
        ranked = sorted(visible, key=self._score, reverse=True)[:limit]
        return tuple(
            SearchHit(
                extraction_id=item.extraction_id,
                tier=item.tier,
                similarity=item.similarity,
                score=self._score(item),
                source=item.source,
                confidence=item.confidence,
                freshness=item.freshness,
                workflow=item.workflow,
                warning="Disputed record; inspect conflicting alternatives."
                if item.workflow is Workflow.DISPUTED
                else None,
            )
            for item in ranked
        )

    @staticmethod
    def _score(candidate: SearchCandidate) -> float:
        tier_weight = {"gold": 1.2, "extracted": 1.0, "normalized": 0.8}.get(candidate.tier, 0.7)
        freshness_weight = 1.0 if candidate.freshness is Freshness.CURRENT else 0.85
        return candidate.similarity * tier_weight * freshness_weight
