"""Claude structured-output wrapper and extraction result contract."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from cognitio_extraction.prompt import Chunk, DocContextHeader, PromptBuilder
from cognitio_extraction.schema import ExtractionEnvelope
from cognitio_extraction.validator import SpanVerifier


@dataclass(frozen=True)
class NormalizedDocument:
    id: UUID
    source_version_id: UUID
    normalized_text: str


@dataclass(frozen=True)
class CostEvent:
    tenant_id: UUID
    model: str
    input_tokens: int
    output_tokens: int
    amount_usd: Decimal
    source_item_id: UUID | None = None
    job_id: UUID | None = None


@dataclass(frozen=True)
class ExtractionResult:
    envelope: ExtractionEnvelope
    cost: CostEvent


class StructuredClaudeClient(Protocol):
    async def extract(
        self,
        *,
        model: str,
        system: str,
        user: str,
        output_schema: dict[str, object],
    ) -> tuple[dict[str, object], int, int]: ...


class Extractor(Protocol):
    async def extract(
        self,
        tenant_id: UUID,
        document: NormalizedDocument,
        chunk: Chunk,
        context: DocContextHeader,
    ) -> ExtractionResult: ...


class ClaudeExtractor:
    def __init__(
        self,
        client: StructuredClaudeClient,
        *,
        model: str = "claude-sonnet-4-6",
        prompt_builder: PromptBuilder | None = None,
        verifier: SpanVerifier | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._prompt_builder = prompt_builder or PromptBuilder()
        self._verifier = verifier or SpanVerifier()

    async def extract(
        self,
        tenant_id: UUID,
        document: NormalizedDocument,
        chunk: Chunk,
        context: DocContextHeader,
    ) -> ExtractionResult:
        prompt = self._prompt_builder.build(context, chunk)
        raw, input_tokens, output_tokens = await self._client.extract(
            model=self._model,
            system=prompt.system,
            user=prompt.user,
            output_schema=ExtractionEnvelope.model_json_schema(),
        )
        envelope = ExtractionEnvelope.model_validate(raw)
        if envelope.source.source_version_id != document.source_version_id:
            raise ValueError("Claude response references a different source version")
        if envelope.source.chunk_id != chunk.chunk_id:
            raise ValueError("Claude response references a different chunk")
        source_mismatch = (
            envelope.source.connector != context.connector
            or envelope.source.source_id != context.source_id
        )
        if source_mismatch:
            raise ValueError("Claude response references a different source")
        self._verifier.verify_envelope(document.normalized_text, envelope)
        cost = CostEvent(
            tenant_id=tenant_id,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            amount_usd=price(self._model, input_tokens, output_tokens),
        )
        # Storage integration persists this one cost row with the extraction transaction.
        return ExtractionResult(envelope=envelope, cost=cost)


def price(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    rates: dict[str, tuple[Decimal, Decimal]] = {
        "claude-sonnet-4-6": (Decimal("3") / 1_000_000, Decimal("15") / 1_000_000),
        "claude-haiku-4-5-20251001": (
            Decimal("1") / 1_000_000,
            Decimal("5") / 1_000_000,
        ),
    }
    try:
        input_rate, output_rate = rates[model]
    except KeyError as error:
        raise ValueError(f"No pricing configured for model {model!r}") from error
    return input_rate * input_tokens + output_rate * output_tokens
