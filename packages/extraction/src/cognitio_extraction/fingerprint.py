"""Deterministic extraction fingerprints for idempotent writes."""

from __future__ import annotations

from hashlib import sha256
from uuid import UUID

from cognitio_extraction.schema import EvidenceSpan


def fingerprint(
    node_type: str,
    normalized_claim: str,
    spans: tuple[EvidenceSpan, ...],
    source_version_id: UUID,
) -> bytes:
    span_key = ",".join(f"{span.start_char}:{span.end_char}" for span in spans)
    material = "|".join(
        (node_type, " ".join(normalized_claim.split()).casefold(), span_key, str(source_version_id))
    )
    return sha256(material.encode()).digest()
