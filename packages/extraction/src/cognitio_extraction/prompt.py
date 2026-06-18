"""Prompt construction with a cacheable fixed prefix and variable chunk context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

FIXED_PREFIX = """You extract source-backed company knowledge.
Return only data matching extraction.v1. Every record and relationship must include at least one
evidence span whose offsets index the exact normalized document text. Distinguish proposals from
decisions, suggestions from commitments, and confidence from trust. Do not invent missing context.
"""


@dataclass(frozen=True)
class DocContextHeader:
    connector: str
    source_id: str
    source_version_id: UUID
    title: str
    source_timestamp: datetime | None
    language: str | None


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    start_char: int
    end_char: int
    text: str


@dataclass(frozen=True)
class ExtractionPrompt:
    system: str
    user: str


class PromptBuilder:
    def build(self, context: DocContextHeader, chunk: Chunk) -> ExtractionPrompt:
        timestamp = context.source_timestamp.isoformat() if context.source_timestamp else "unknown"
        header = (
            f"connector={context.connector}\nsource_id={context.source_id}\n"
            f"source_version_id={context.source_version_id}\ntitle={context.title}\n"
            f"source_timestamp={timestamp}\nlanguage={context.language or 'unknown'}\n"
            f"chunk_id={chunk.chunk_id}\nchunk_offsets={chunk.start_char}:{chunk.end_char}"
        )
        return ExtractionPrompt(system=FIXED_PREFIX, user=f"{header}\n\n{chunk.text}")
