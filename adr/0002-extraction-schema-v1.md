# 0002. `extraction.v1` structured-output schema with required evidence spans

- Status: Accepted
- Date: 2026-06-17

## Context

The product's entire value proposition is **trustworthy, source-backed knowledge**. That is only
credible if every extracted record can be traced to the exact text it came from, and if model output
cannot silently corrupt storage.

Two failure modes make a naive approach unworkable:

- **Free-text parsing of LLM output** is brittle — it breaks on formatting drift and admits malformed
  records into storage.
- **Provenance against the mutable source** is meaningless — the source can change after extraction,
  so an offset into "the Notion page" may point at different text later.

We also need idempotency: re-running extraction over an unchanged chunk must not create duplicates,
and the incremental cascade must be able to tell "same fact" from "changed fact" deterministically.

## Decision

Adopt a single, versioned structured-output schema — **`extraction.v1`** — and enforce it as a hard
boundary. Concretely:

- Extraction uses Claude **structured outputs**; we never parse free text.
- The schema defines one response envelope per normalized document/chunk containing `entities`,
  `decisions`, `actions`, `facts`, `open_questions`, `relationships`, and `warnings`.
- **`evidence_spans` are REQUIRED on every extracted record**, as `{start_char, end_char, text}`
  offsets into the **exact, immutable normalized text version** — not the mutable source.
- Every response is validated against the JSON Schema / Pydantic models **before any write**;
  malformed JSON goes through a bounded repair/retry path, then dead-letters.
- An **offset-first evidence-span verifier** runs on every record: `start_char`/`end_char` are
  authoritative; `text` is a checksum compared after Unicode + whitespace normalization (exact byte
  matching would false-reject on trivial differences).
- Each record gets a deterministic fingerprint `hash(type + normalized_claim + evidence_span +
  source_version_id)` for idempotency and change detection.
- `local_id`s are scoped to one model response and mapped to durable DB ids **only after** the whole
  response validates. Entity *mentions* are produced here; resolution to canonical `entities` is a
  separate pass. Confidence is **per-record-type**, never a single node-level float, and is **not**
  trust.

The schema is **versioned** (`schema.v1`) so future changes are additive and traceable; the canonical
field-by-field definition lives in `DESIGN.md` → AI Extraction Pipeline, and `cognitio_extraction`
enforces it.

## Consequences

- **Easier:** storage integrity is guaranteed — a bad prompt produces zero or rejected records, never
  corrupt ones; review can always show evidence next to source; the incremental cascade can diff
  facts deterministically by fingerprint.
- **Easier:** model/prompt iteration is safe behind the validation boundary, and the golden eval set
  can be scored against a fixed schema.
- **Harder / accepted:** requiring evidence spans on *every* record raises the bar on the model and
  the verifier; some genuinely-true extractions with imperfect spans are rejected. We accept higher
  precision at some recall cost — trust first.
- **Accepted:** schema evolution requires a new version (`extraction.v2`) and a migration story rather
  than ad-hoc field additions.
