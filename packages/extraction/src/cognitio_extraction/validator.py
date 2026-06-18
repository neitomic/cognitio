"""Offset-first evidence verification against immutable normalized text."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from cognitio_extraction.schema import EvidenceSpan, ExtractionEnvelope


class VerifyStatus(StrEnum):
    VERIFIED = "verified"
    OUT_OF_RANGE = "out_of_range"
    TEXT_MISMATCH = "text_mismatch"


@dataclass(frozen=True)
class VerifyOutcome:
    status: VerifyStatus
    observed_text: str | None = None

    @property
    def valid(self) -> bool:
        return self.status is VerifyStatus.VERIFIED


class SpanVerificationError(ValueError):
    pass


class EvidenceBearing(Protocol):
    evidence_spans: tuple[EvidenceSpan, ...]


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", normalized).strip().casefold()


class SpanVerifier:
    def verify(self, normalized_text: str, span: EvidenceSpan) -> VerifyOutcome:
        if span.start_char >= span.end_char or span.end_char > len(normalized_text):
            return VerifyOutcome(VerifyStatus.OUT_OF_RANGE)
        observed = normalized_text[span.start_char : span.end_char]
        if _normalize(observed) != _normalize(span.text):
            return VerifyOutcome(VerifyStatus.TEXT_MISMATCH, observed)
        return VerifyOutcome(VerifyStatus.VERIFIED, observed)

    def verify_envelope(self, normalized_text: str, envelope: ExtractionEnvelope) -> None:
        records: tuple[EvidenceBearing, ...] = (
            *envelope.entities,
            *envelope.decisions,
            *envelope.actions,
            *envelope.facts,
            *envelope.open_questions,
            *envelope.relationships,
        )
        for record in records:
            for span in record.evidence_spans:
                outcome = self.verify(normalized_text, span)
                if not outcome.valid:
                    raise SpanVerificationError(
                        f"{record.__class__.__name__} has invalid evidence span: {outcome.status}"
                    )
