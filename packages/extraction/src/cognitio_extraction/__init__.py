"""Structured, evidence-backed extraction."""

from cognitio_extraction.client import ClaudeExtractor, Extractor
from cognitio_extraction.schema import EvidenceSpan, ExtractionEnvelope

__all__ = ["ClaudeExtractor", "EvidenceSpan", "ExtractionEnvelope", "Extractor"]
