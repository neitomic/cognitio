"""Pydantic implementation of the versioned `extraction.v1` contract."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal
from uuid import UUID

from cognitio_storage.enums import EntityType
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceSpan(StrictModel):
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_offsets(self) -> EvidenceSpan:
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")
        return self


class ExtractionSource(StrictModel):
    connector: str
    source_id: str
    source_version_id: UUID
    chunk_id: str
    title: str


class EvidenceBackedRecord(StrictModel):
    local_id: str = Field(min_length=1)
    evidence_spans: tuple[EvidenceSpan, ...] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class EntityRecord(EvidenceBackedRecord):
    name: str
    type: EntityType
    aliases: tuple[str, ...] = ()
    description: str | None = None


class DecisionStatus(StrEnum):
    PROPOSED = "proposed"
    DECIDED = "decided"
    REVERSED = "reversed"
    SUPERSEDED = "superseded"
    UNKNOWN = "unknown"


class DecisionRecord(EvidenceBackedRecord):
    title: str
    decision: str
    status: DecisionStatus
    decision_date: date | None = None
    decision_makers: tuple[str, ...] = ()
    affected_entities: tuple[str, ...] = ()
    rationale: str | None = None
    constraints: tuple[str, ...] = ()


class ActionStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class SourceLanguage(StrEnum):
    IMPERATIVE = "imperative"
    COMMITMENT = "commitment"
    SUGGESTION = "suggestion"
    INFERRED = "inferred"


class ActionRecord(EvidenceBackedRecord):
    description: str
    owner_entities: tuple[str, ...] = ()
    status: ActionStatus
    due_date: date | None = None
    related_entities: tuple[str, ...] = ()
    source_language: SourceLanguage


class ClaimType(StrEnum):
    STATE = "state"
    METRIC = "metric"
    POLICY = "policy"
    OWNERSHIP = "ownership"
    DEPENDENCY = "dependency"
    TIMELINE = "timeline"
    RISK = "risk"
    OTHER = "other"


class Certainty(StrEnum):
    CERTAIN = "certain"
    LIKELY = "likely"
    UNCERTAIN = "uncertain"


class FactQualifiers(StrictModel):
    time_scope: str | None = None
    certainty: Certainty
    scope: str | None = None


class FactRecord(EvidenceBackedRecord):
    claim: str
    claim_type: ClaimType
    subject_entities: tuple[str, ...] = ()
    qualifiers: FactQualifiers


class QuestionStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    UNKNOWN = "unknown"


class OpenQuestionRecord(EvidenceBackedRecord):
    question: str
    related_entities: tuple[str, ...] = ()
    status: QuestionStatus


class RelationshipType(StrEnum):
    MENTIONS = "mentions"
    AFFECTS = "affects"
    ASSIGNS = "assigns"
    DEPENDS_ON = "depends_on"
    SUPERSEDES = "supersedes"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"


class RelationshipRecord(StrictModel):
    from_local_id: str
    to_local_id: str
    type: RelationshipType
    evidence_spans: tuple[EvidenceSpan, ...] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class WarningCode(StrEnum):
    AMBIGUOUS_OWNER = "ambiguous_owner"
    RELATIVE_DATE = "relative_date"
    MISSING_CONTEXT = "missing_context"
    TRUNCATED_INPUT = "truncated_input"
    LOW_SIGNAL = "low_signal"


class ExtractionWarning(StrictModel):
    code: WarningCode
    message: str


class ExtractionEnvelope(StrictModel):
    schema_version: Literal["extraction.v1"] = "extraction.v1"
    source: ExtractionSource
    entities: tuple[EntityRecord, ...] = ()
    decisions: tuple[DecisionRecord, ...] = ()
    actions: tuple[ActionRecord, ...] = ()
    facts: tuple[FactRecord, ...] = ()
    open_questions: tuple[OpenQuestionRecord, ...] = ()
    relationships: tuple[RelationshipRecord, ...] = ()
    warnings: tuple[ExtractionWarning, ...] = ()

    @model_validator(mode="after")
    def validate_local_references(self) -> ExtractionEnvelope:
        records = (
            *self.entities,
            *self.decisions,
            *self.actions,
            *self.facts,
            *self.open_questions,
        )
        local_ids = [record.local_id for record in records]
        if len(local_ids) != len(set(local_ids)):
            raise ValueError("local_id values must be unique within one extraction response")

        known_ids = set(local_ids)
        for relationship in self.relationships:
            if relationship.from_local_id not in known_ids:
                raise ValueError(
                    f"Unknown relationship source local_id {relationship.from_local_id!r}"
                )
            if relationship.to_local_id not in known_ids:
                raise ValueError(
                    f"Unknown relationship target local_id {relationship.to_local_id!r}"
                )
        return self
