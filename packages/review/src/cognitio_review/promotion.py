"""Promotion routing and the deliberately disabled Phase 1 auto-promotion gate."""

from __future__ import annotations

from dataclasses import dataclass

from cognitio_storage.enums import NodeType

from cognitio_review.types import ReviewDisposition


@dataclass(frozen=True)
class PromotionCandidate:
    node_type: NodeType
    confidence: float | None
    claim_type: str | None
    exact_span_verified: bool
    authoritative_source: bool
    unresolved_language: bool
    conflicts_with_gold: bool
    deterministic_validation_passed: bool


class PromotionPolicy:
    def route(self, candidate: PromotionCandidate) -> ReviewDisposition:
        """Phase 1 routes every extraction to a human; risky records are hard-gated."""
        high_risk = candidate.node_type in {NodeType.DECISION, NodeType.ACTION}
        if high_risk or candidate.confidence is None or candidate.confidence < 0.5:
            return ReviewDisposition.HARD_GATE
        return ReviewDisposition.SOFT_REVIEW

    def auto_promote_eligible(self, candidate: PromotionCandidate) -> bool:
        # TODO(Phase 2): activate only after conflict detection exists and is evaluated.
        return False
