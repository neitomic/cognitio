# Cognitio — Design Document

## Vision

A living knowledge platform for companies. Static RAG systems index documents once and drift out of date; Cognitio instead maintains a continuously updated, tiered knowledge graph where content from documents, discussions, and comments flows in incrementally and is progressively distilled into higher-quality, structured knowledge.

The model is a **collaborative pair**: AI proposes enrichments and connections; humans confirm or override the high-stakes ones. Over time the graph becomes more accurate, more connected, and more useful — not less, as it would with a stale index.

**What this actually is.** At its core, Cognitio is a *source-backed extraction and review system with semantic search*. The differentiating, hard work is building trustworthy extraction records with exact provenance, keeping connectors synchronized despite messy source APIs, and making review efficient enough that "Gold" knowledge means something. The richer graph features come *after* users trust the extracted knowledge — not before.

---

## Core Concepts

### Tiers

Knowledge is organized into **tiers** reflecting how processed and trusted it is. Tiers are a conceptual ladder; they are **not** a single polymorphic table. Each tier maps to typed, purpose-built tables (see Data Model).

| Tier | Name | Description |
|------|------|-------------|
| 0 | **Raw** | Immutable raw snapshots from source systems (a Notion page, a Slack thread). Changes create a new version, never an in-place edit. Stored in `source_versions`. |
| 1 | **Normalized** | Cleaned, normalized text with **stable chunk boundaries and character offsets**, extracted metadata, detected language. Still 1:1 with a source version. Stored in `normalized_documents`. |
| 2 | **Extracted** | Typed records pulled out by the model — decisions, actions, facts, entities, open questions — **each carrying required evidence spans (character offsets into the normalized text)**. This is *source-backed extracted knowledge*, not yet authoritative. Stored in `extractions`. |
| 3 | **Gold** | Curated, authoritative knowledge. An extraction reaches Gold by human confirmation, by an authoritative source, or (Phase 2+) by *narrowly* scoped auto-promotion. Gold is a first-class `trust_state` on the extraction record, not a separate table. |
| 4 | **Synthesized** | Cross-source summaries and derived insights spanning multiple Gold records. Expensive; deferred to Phase 3. |

Tier 0 and Tier 1 are produced in one cheap pipeline step but kept as distinct rows: chunk offsets (Tier 1) must be stable and independent of the raw bytes (Tier 0).

### Edges

Edges are **typed relationships**, each with its own confidence and provenance:

- `derived_from` — an extraction derives from a source version / normalized chunk
- `references` — an explicit mention (parsed @-mention, link)
- `supersedes` — a newer record replaces an older one (cross-record replacement only; intra-record history is handled by version fields)
- `supports` / `contradicts` — two records agree or conflict; **inferred, so each carries its own confidence** and provenance
- `related_to` — semantic similarity; **computed at query time in Phase 1, not stored**

**`related_to` is computed, not materialized, early on.** Similarity edges are noisy, high-volume, and model/version-dependent. Persisting them eagerly produces a hairball that makes traversal meaningless and pins the graph to one embedding model. We compute semantic neighbors at query time from the `embeddings` table with a stated similarity floor, and materialize `related_to` only if a concrete latency/UX need is measured.

**Write-time discipline for materialized `supports` / `contradicts`.** Unlike `related_to`, these *are* persisted, so they need an explicit guard against the same hairball at write time — a heavily-echoed Gold fact must not sprout hundreds of `supports` edges. Two limits apply when materializing them:

- **Minimum confidence threshold for materialization** — `supports` ≥ **0.7**, `contradicts` ≥ **0.8** (the higher bar reflects that a false `contradicts` corrupts answers and floods the dispute queue). Below threshold the relationship is not persisted.
- **Max-edges-per-node cap** — ≤ **50** `supports` edges per Gold fact and ≤ **20** `contradicts` edges; when the cap is hit, the lowest-confidence edges are dropped (or never written) so only the strongest relationships survive.

Both sets of numbers are **starting points to be tuned against eval data**, not fixed constants. `related_to` stays computed-not-stored as above and is unaffected by these caps.

Because `edges` span every node type (`from_type`/`to_type`), they cannot carry DB-level foreign keys; referential integrity is an application invariant. A periodic **edge-integrity / orphan-GC job** prunes edges pointing at deleted, superseded, or archived rows — this is a correctness concern, not just hygiene, because ACL and dispute logic both traverse edges, and a dangling `contradicts` edge produces wrong answers.

---

## Data Model

The data model uses purpose-built typed tables. Core fields (extraction type, evidence span, action owner, due date, decision status, review state, trust state, ACL) are **first-class columns or schema-validated typed JSON** — not freeform `jsonb`. `metadata: jsonb` remains only for connector-specific fields.

**Present on every row from day 1:** `tenant_id`, version fields (`version` + indexed `is_current`), and `node_type` where applicable. These are cheap now and painful to retrofit later.

### Status: three orthogonal axes

A single overloaded `status` enum admits illegal combinations. Status is split into three independent fields on the relevant records:

| Axis | Field | Values | Meaning |
|------|-------|--------|---------|
| **Lifecycle** | `lifecycle` | `active` \| `archived` | Is this record part of the live knowledge base? |
| **Freshness** | `freshness` | `current` \| `stale` | Does this reflect the latest source version, or is it queued for re-derivation? |
| **Workflow** | `workflow` | `none` \| `pending_review` \| `disputed` | Where is this in the human/conflict workflow? |

A record can legitimately be `active` + `stale` + `pending_review` at once — three axes, no illegal states.

### Trust state (Gold)

Gold is the product's headline concept, so it is a **first-class, indexable state**, not an implicit join against review history. The `extractions.trust_state` column carries `extracted | gold | superseded`, with `gold_source` recording *how* it became Gold (`human_review | authoritative_source | auto_promoted`). This lets "show me the authoritative decisions" be a single indexed query, and lets a record be Gold-by-authoritative-source without a synthetic review row.

### Schema

```
source_items {
  id, tenant_id, node_type,
  connector, source_id,            -- stable external id
  source_url, current_version_id,
  source_revision,                 -- monotonic; writes can't regress it
  acl,                             -- captured access descriptor (principals/groups)
  lifecycle,                       -- active | archived
  created_at, updated_at
}

source_versions {                  -- Tier 0: immutable raw snapshots
  id, tenant_id, source_item_id,
  content_hash,                    -- sha256 of raw bytes
  raw_content, fetched_metadata, acl_snapshot,
  source_timestamp, fetched_at,
  is_current
}

normalized_documents {             -- Tier 1: normalized + stable chunks
  id, tenant_id, source_version_id,
  normalized_text,                 -- offsets are stable against this
  language,
  chunks: [ { chunk_id, start_char, end_char, chunk_hash } ],
  is_current, created_at
}

extractions {                      -- Tier 2 / Tier 3: typed extracted records
  id, tenant_id, node_type,        -- decision | action | fact | entity_ref | open_question
  source_version_id, normalized_document_id, chunk_id,
  payload,                         -- schema-validated typed JSON (extraction.v1)
  -- indexed generated columns promoted from payload for cheap structured queries:
  owner_entity_id, due_date, item_status, claim_type,
  evidence_spans: [ {start_char,end_char,text} ],   -- REQUIRED
  fingerprint,                     -- hash(type+normalized_claim+span+source_version_id)
  confidence,                      -- per-extraction
  effective_acl,                   -- union of source denies (= intersection of source allows)
  trust_state,                     -- extracted | gold | superseded
  gold_source,                     -- human_review | authoritative_source | auto_promoted | null
  lifecycle, freshness, workflow,  -- three orthogonal axes
  version, is_current,
  created_at
}

entity_mentions {                  -- mention spans in source text
  id, tenant_id, extraction_id, normalized_document_id,
  surface_form, span: {start_char,end_char,text},
  resolved_entity_id,              -- null until resolution pass
  confidence
}

entities {                         -- Tier 3 canonical, post-resolution
  id, tenant_id, node_type,        -- person | team | product | system | ...
  canonical_name, aliases,         -- public-ish identity (not ACL-restricted)
  attributes: [                    -- provenance-scoped, ACL-bearing facts about the entity
    { value, source_version_id, effective_acl }
  ],
  lifecycle, version, is_current, created_at
}

entity_merges {                    -- audit + reversibility for merge/split
  id, tenant_id, operation,        -- merge | split
  surviving_entity_id, merged_entity_ids,
  reassigned_mention_ids,
  performed_by, performed_at
}

edges {                            -- typed relationships (no FKs: app-enforced integrity)
  id, tenant_id,
  from_id, from_type, to_id, to_type,
  type,                            -- derived_from | references | supersedes | supports | contradicts
  confidence,                      -- edges are inferred → own confidence
  provenance,                      -- human | model | vector | parser
  reviewer_id,                     -- which human, if human-created
  evidence_spans,
  valid_from, valid_to,            -- temporal validity of the underlying fact
  created_at
}

conflicts {                        -- first-class resolution unit
  id, tenant_id,
  member_ids: [ ... ],             -- records in the conflict set
  contradicts_edge_ids: [ ... ],
  detector_confidence,
  proposed_resolution, status,     -- open | resolved
  resolved_by, resolved_at
}

review_items {                     -- workflow + audit trail
  id, tenant_id, target_id, target_type,
  workflow,                        -- pending_review | disputed | ...
  reviewer_id, decision,           -- confirm | edit | reject
  before, after,                   -- captured for eval / override-rate
  cost_attributed,
  created_at, decided_at
}

embeddings {                       -- separate table, version-aware
  id, tenant_id,
  object_type, object_id,          -- what is embedded
  model, model_version,            -- queries pin to one version
  vector, created_at
}

change_events {                    -- per-source, idempotent
  id, tenant_id, connector, source_id,
  cursor, high_watermark,
  change_type,                     -- created | updated | deleted | permission_changed
  source_revision,
  status,                          -- pending | processing | done | failed | dead_letter
  attempts, next_retry_at,
  processed_at
}

principals {                       -- Cognitio identity ↔ per-source identities
  id, tenant_id, cognitio_user_id,
  source_identities: [ { connector, source_user_id } ],
  group_memberships_cache, cache_refreshed_at
}
```

**Notes:**

- **Versioning** is explicit (`version` + indexed `is_current`), so "current only" queries never traverse `supersedes` chains; `supersedes` is reserved for cross-record replacement.
- **Payload indexing.** Operative fields (`owner_entity_id`, `due_date`, `item_status`, `claim_type`) are promoted to indexed generated columns so queries like "all open action items with owners" are cheap; remaining payload fields use GIN indexes. Typed JSON gives validation; generated columns give query performance.
- **Embeddings are version-aware in their own table.** Mixed-version vectors are incomparable in a single ANN index (HNSW indexes one space), so re-embedding uses a **blue/green rebuild** and queries **pin to one embedding version** (see Embedding Upgrades).
- **Temporal validity** (`valid_from`/`valid_to` on facts/edges) lets synthesis avoid mixing expired and current claims ("deprecate v1 by Q3" is true until Q3).
- **Entity identity vs. attributes.** A canonical entity's *name/aliases* are treated as public-ish (entity existence is rarely sensitive), while *descriptions and other derived attributes* are stored per-source with their own ACL — so a private mention's attribute can't leak through the shared canonical record.

---

## Incremental Indexing

The incremental update path is the core differentiator and ships in Phase 1. When a source changes:

1. **Delta detection.** Connectors return changes via cursor-based, capability-aware scans (see Source Connectors). `normalized_documents` store per-chunk boundaries and **per-chunk hashes**, so only changed chunks are reprocessed — not the whole document. A whole-document hash only says *that* something changed; chunk hashes make the "cheap targeted update" claim real.
2. **Monotonic write ordering.** Each `source_item` tracks a monotonic source revision. Writes that would regress it are rejected, preventing out-of-order `fetch` results (from retries or parallel workers) from overwriting newer content.
3. **Invalidation propagation.** Extractions derived from a changed chunk are flagged `freshness = stale` **per-record** and queued for re-derivation. Each per-node flag is cleared only when *that* node's re-derivation commits — so a crashed cascade is **resumable**, not all-or-nothing.
4. **Re-derivation.** The model re-extracts from updated chunks. New extractions are compared to prior ones via deterministic fingerprints: unchanged facts kept, changed facts versioned, new facts added.
5. **Conflict detection (Phase 2).** A re-derived fact that contradicts existing Gold opens a dispute (see Human-in-the-Loop) rather than overwriting.
6. **Downstream cascade (Phase 3).** Synthesized nodes depending on changed Gold are re-queued — asynchronously and rate-limited, never synchronously per change.

**Idempotency.** Every extracted record gets a deterministic fingerprint `hash(type + normalized_claim + evidence_span + source_version_id)`. Re-running a derivation is a no-op if the fingerprint already exists. Cascade steps are individually tracked and idempotent.

**Connector health & reconciliation.** Webhooks are at-least-once and lossy, so they are a *latency optimization only*. Periodic `incremental_scan` / full reconciliation is the source of truth. Connectors track sync state, high-watermark cursor, and health; a wedged connector (token expiry, outage) is distinguishable from "no changes" and alerts (see Observability).

**Deletion.** A `deleted` change (discovered via `tombstone_scan`) does **not** trigger re-derivation — the content is gone. Derived records are marked `lifecycle = archived` but retained, since a deleted source may still hold valid facts. Right-to-deletion is different and forces hard removal (see Access Control).

---

## Source Connectors

A clean `list_changes(since)` interface is insufficient for real SaaS APIs: timestamps are non-monotonic/rounded/timezone-weird, pagination reorders results mid-scan, some APIs lack delta queries, deletions are hard to discover, and permission changes are content-invisible changes. The connector contract is therefore **cursor-based and capability-aware**:

```python
class Connector:
    def capabilities(self) -> ConnectorCapabilities: ...
    async def full_scan(self, cursor: str | None) -> Page[SourceRef]: ...
    async def incremental_scan(self, cursor: str | None) -> Page[ChangeEvent]: ...
    async def fetch(self, ref: SourceRef) -> SourceSnapshot: ...
    async def fetch_children(self, ref: SourceRef, cursor: str | None) -> Page[SourceRef]: ...
    async def tombstone_scan(self, cursor: str | None) -> Page[Tombstone]: ...
```

Each `Page` returns `items`, `next_cursor`, `high_watermark`, `sync_started_at`, `has_more`, and `retry_after`. **Capabilities** declare: incremental cursor support, updated-since filter, webhooks, tombstones, permission metadata, child expansion, and stable content hashes. Sources without delta queries fall back to periodic full scans with content hashing — acceptable for small workspaces, but must be scoped by database/channel/folder for large ones.

Per-connector sync strategies differ:
- **Notion** — block-tree traversal + `last_edited_time` + block-children fetch + periodic reconciliation.
- **Slack** — conversations history + replies; edited/deleted messages need the events API or reconciliation windows.
- **Google Drive** — cursor-based changes API; document content fetch is separate.
- **GitHub** — webhooks + REST/GraphQL updated cursors; comments/reviews have their own change surfaces.
- **Confluence** — page version APIs help; comments, attachments, and permissions need explicit treatment.

**From day 1, every connector needs:** a stored sync checkpoint, idempotent change events, content hashes to skip no-op fetches, retry/backoff + dead-letter state, periodic reconciliation, and tombstone/permission-loss handling. Without these even the first connector drifts.

**Phase 1 connector:** Notion only.

---

## AI Extraction Pipeline

Model output is **untrusted until schema validation and evidence-span verification pass.** Extraction uses Claude **structured outputs** to guarantee parseable JSON — never free-text parsing. Model tiering controls cost: Haiku for simple/entity extraction, Sonnet for decisions, policies, conflict detection, and synthesis.

### Structured output schema (`extraction.v1`)

One response envelope per normalized document/chunk. **`evidence_spans` are required for every extracted record** — character offsets into the *exact, immutable normalized text version*, not the mutable source.

```json
{
  "schema_version": "extraction.v1",
  "source": {
    "connector": "notion",
    "source_id": "external-page-id",
    "source_version_id": "uuid",
    "chunk_id": "uuid",
    "title": "string"
  },
  "entities": [
    {
      "local_id": "ent_1",
      "name": "v1 API",
      "type": "person|team|product|system|customer|vendor|project|repository|document|metric|other",
      "aliases": ["string"],
      "description": "short source-backed description or null",
      "evidence_spans": [{ "start_char": 128, "end_char": 134, "text": "v1 API" }],
      "confidence": 0.0
    }
  ],
  "decisions": [
    {
      "local_id": "dec_1",
      "title": "Deprecate v1 API by Q3",
      "decision": "The team decided to deprecate the v1 API by Q3.",
      "status": "proposed|decided|reversed|superseded|unknown",
      "decision_date": "2026-06-17|null",
      "decision_makers": ["ent_2"],
      "affected_entities": ["ent_1"],
      "rationale": "short source-backed rationale or null",
      "constraints": ["string"],
      "evidence_spans": [{ "start_char": 420, "end_char": 476, "text": "we're deprecating v1 API by Q3" }],
      "confidence": 0.0
    }
  ],
  "actions": [
    {
      "local_id": "act_1",
      "description": "Alice will write the migration guide.",
      "owner_entities": ["ent_3"],
      "status": "open|in_progress|done|blocked|cancelled|unknown",
      "due_date": "2026-09-30|null",
      "related_entities": ["ent_1"],
      "source_language": "imperative|commitment|suggestion|inferred",
      "evidence_spans": [{ "start_char": 500, "end_char": 536, "text": "@alice to write migration guide" }],
      "confidence": 0.0
    }
  ],
  "facts": [
    {
      "local_id": "fact_1",
      "claim": "The v1 API is still used by enterprise customers.",
      "claim_type": "state|metric|policy|ownership|dependency|timeline|risk|other",
      "subject_entities": ["ent_1"],
      "qualifiers": {
        "time_scope": "current|null",
        "certainty": "certain|likely|uncertain",
        "scope": "all customers|enterprise customers|null"
      },
      "evidence_spans": [{ "start_char": 220, "end_char": 268, "text": "enterprise customers still rely on v1" }],
      "confidence": 0.0
    }
  ],
  "open_questions": [
    {
      "local_id": "q_1",
      "question": "Who owns the migration guide?",
      "related_entities": ["ent_1"],
      "status": "open|answered|unknown",
      "evidence_spans": [{ "start_char": 600, "end_char": 636, "text": "who owns the migration guide?" }],
      "confidence": 0.0
    }
  ],
  "relationships": [
    {
      "from_local_id": "dec_1",
      "to_local_id": "ent_1",
      "type": "mentions|affects|assigns|depends_on|supersedes|supports|contradicts",
      "evidence_spans": [{ "start_char": 420, "end_char": 476, "text": "we're deprecating v1 API by Q3" }],
      "confidence": 0.0
    }
  ],
  "warnings": [
    { "code": "ambiguous_owner|relative_date|missing_context|truncated_input|low_signal", "message": "string" }
  ]
}
```

### Implementation rules

- Require `evidence_spans` for every extraction.
- Offsets are against the exact normalized text version (immutable), not the source.
- `local_id`s are scoped to one model response; map to durable DB IDs only after validation.
- Compute a deterministic fingerprint `hash(type + normalized_claim + evidence_span + source_version_id)` for idempotency.
- Validate every response with JSON Schema / Pydantic before any write; malformed JSON goes through a repair/retry path.
- **Entity mentions are separate from canonical entities** — extraction produces `entity_mentions`; resolution to `entities` is a separate pass.
- Confidence is **per-extraction-type** (one per record), not a single node-level float.

**Evidence-span verification (with tolerance).** The verifier is **offset-first**: `start_char`/`end_char` against the immutable normalized text are authoritative, and the `text` field is treated as a checksum compared after Unicode and whitespace normalization. Exact byte matching would falsely reject on whitespace, Unicode form, or trivial paraphrase even when offsets are correct, turning the verifier into a high-false-reject gate. Records whose offsets are out of range, or whose normalized span text diverges materially from the model's `text`, are rejected.

### Known failure modes (handled explicitly)

Decisions vs. proposals ("we should" ≠ "we decided"); relative dates needing source timestamp + timezone; implicit owners ("Alice can take this" may be a suggestion); pronouns / missing thread context; duplicate facts across sources; entity ambiguity ("Platform", "Core", first names); stale source truth; permission leaks via summaries; table/list/row semantics in Notion DBs and Sheets; low-signal extraction spam; over-normalization erasing operative wording.

**Chunk-boundary context loss** is mitigated with overlapping chunk windows plus a parent-document context header passed to the extractor, so actions/decisions that depend on context outside a single chunk are not silently lost.

---

## Human-in-the-Loop

### Review flow

Reviewers see a queue of extracted records grouped by topic, each shown **next to its source evidence**. One-click confirm promotes to Gold (`trust_state = gold`, `gold_source = human_review`); edit + confirm produces corrected Gold (the override captured as an eval signal); reject discards the record as a negative example. Every action writes to the `review_items` audit trail — which reviewer, when, and what changed.

### Auto-promotion rules

Model confidence is **not calibrated** and must not, on its own, carry trust. A model is routinely *confidently wrong* about owners, dates, and decision-vs-proposal.

**Phase 1: no auto-promotion at all.** Gold is reached only by human confirmation. The MVP optimizes for trustworthy, evidence-first review.

**Phase 2+: narrow auto-promotion of low-risk simple facts only.** Decisions, policies, ownership, deadlines, and customer commitments are **never** auto-promoted on confidence alone. To auto-promote, a record must satisfy **all** of:

- confidence ≥ 0.9, **and**
- has an exact, verified evidence span, **and**
- comes from a source type **allowed to be authoritative** (not, e.g., an untrusted comment), **and**
- has **no unresolved pronouns or relative dates**, **and**
- has **no conflict** with existing Gold (requires conflict detection — hence Phase 2), **and**
- passes deterministic validation.

Only `facts` of low-risk `claim_type` (e.g. `state`, simple `metric`) qualify. Everything else routes to human review.

| Level | Trigger | Action |
|-------|---------|--------|
| **Auto-promote** (Phase 2+) | All gates above pass, low-risk fact only | Extracted → Gold, logged with full provenance |
| **Soft review** | Plausible but not gate-passing | `workflow = pending_review`, shown in queue |
| **Hard gate** | Low confidence, high-risk type, or conflict | Stays Extracted, blocks until a human acts |

### Conflict & dispute lifecycle

Contradiction handling is engineered, not assumed.

**Contradiction detection is its own step.** "Contradicts" between two natural-language facts is an LLM/NLI classification task with its own error rate, not a primitive. It is a distinct pipeline step (Phase 2) that:

- runs a dedicated classifier (NLI model or LLM with a strict prompt) over candidate fact pairs. Candidates are not generated by an O(n²) all-pairs scan; the same **two-stage blocking** used for entity resolution applies, so only a small candidate set reaches the expensive classifier:
  - **Stage 1 — lexical block.** A `pg_trgm` **GIN** index over normalized claim text surfaces lexically-near facts.
  - **Stage 2 — semantic block.** pgvector **ANN** over fact embeddings, filtered to facts that **share a subject entity**, surfaces semantically-near facts that disagree without sharing wording.
  - Only the union of both blocks is passed pairwise to the contradiction classifier,
- emits its **own confidence score**, separate from extraction confidence,
- has explicit thresholds and a stated policy for *its* false positives (which flood the review queue) and false negatives (which silently corrupt Gold). High-confidence contradictions auto-open a dispute; borderline ones are sampled into the eval set.

Detection runs against existing Gold by default; pre-promotion Extracted-vs-Extracted contradictions are surfaced opportunistically during review rather than auto-disputed.

**`disputed` status and query behavior.** When a re-derived fact contradicts existing Gold:

- the contradicted Gold record's `workflow` becomes **`disputed`** — it is neither silently overwritten nor demoted out of the graph,
- a **`Conflict` record** groups the involved records, the `contradicts` edges, the detector's confidence, and (when proposed) a resolution. Conflicts are a first-class resolution unit, handling multi-way and transitive conflicts as one consistent set,
- at query time a `disputed` record is **still returned, but with a warning and explicitly not as authoritative** — GAG surfaces the conflicting alternatives and the open dispute in provenance rather than asserting one side.

A reviewer resolves the whole conflict set at once (pick a winner, mark records time-scoped, supersede, etc.), which clears `disputed` consistently across the set.

### Entity resolution

A named, separate component — not absorbed into `related_to` similarity. The same decision discussed in Slack, written up in Confluence, and recapped in a meeting note produces multiple raw nodes and near-identical extractions. Without canonicalization, Gold accumulates duplicates and synthesis double-counts ("14 discussions" may be 6 distinct decisions echoed).

**Two-pass design:**

1. **Mention pass (extraction time).** Every entity reference becomes an `entity_mention` row with its span and surface form. Cheap; runs on the extraction model.
2. **Resolution pass (separate, async).** Mentions are clustered to canonical `entities`, then ambiguous clusters go to a resolution model. The critical design point is **candidate generation**: a naïve all-pairs comparison is O(n²) and a resolution-model call per pair is ruinously expensive, so resolution uses an explicit **two-stage blocking** step and only the surviving candidate pairs reach the expensive pairwise comparison:
   - **Stage 1 — lexical block.** A `pg_trgm` **GIN** index on normalized entity names/aliases surfaces name-similar candidates (typos, casing, abbreviations, partial overlaps).
   - **Stage 2 — semantic block.** pgvector **ANN** over entity embeddings surfaces semantically-similar candidates that don't share surface form, filtered to those with a **shared subject entity** to bound the candidate set.
   - Only the union of these two blocks proceeds to the resolution model / pairwise comparison.

**Merge/split** is a first-class, reversible operation recorded in `entity_merges`: a merge reassigns `entity_mentions.resolved_entity_id` to the surviving entity and records the merged ids; a split partitions mentions back out and re-derives affected fingerprints. The audit row makes both reversible. Fact/decision dedup keys off resolved entities + fingerprints.

---

## Access Control

This is a **showstopper for company knowledge** and is designed in from day 1, not bolted on. Cognitio must never let a query surface a fact a user could not see at the source — including facts that flow into derived or synthesized records.

**Vocabulary.** Throughout, the model is **most-restrictive-wins**, framed consistently as the **union of source denies** (equivalently, the intersection of source allow-sets). A derived record is visible only to principals who could see **all** of its sources.

**Design:**

1. **Ingest ACLs at fetch time.** Every `source_version` records the source object's access descriptor (allowed principals/groups, visibility scope) captured when fetched. Permission changes *on an object* are changes and trigger re-fetch even when content is unchanged.
2. **Propagate restrictions through derivation.** Every `extraction` records the source versions it derives from; its `effective_acl` is the union of source denies. A synthesized record carries the intersection of its constituents' viewer sets.
3. **Identity mapping (principals).** A Cognitio user is one principal mapped to its per-source identities (Notion user id, Slack member id, Google identity) in the `principals` table. Enforcement resolves the requesting principal to the relevant source identity before filtering. Phase 1 (single connector, single tenant) can use captured principal lists directly; cross-source identity mapping lands with the second connector in Phase 2.
4. **Group membership resolved live.** Most ACLs are expressed via *groups* (a teamspace, a channel, an IdP group), and membership changes happen in the identity provider or source workspace — **not** on the object — so they do not trigger an object re-fetch. Enforcement therefore **resolves group membership live at query time** (with a short-TTL membership cache in `principals`), rather than trusting a captured snapshot's expanded member list. This avoids both leaking to a removed member and blocking a newly-added one.
5. **Enforce before ranking.** Search and graph traversal filter candidates by the requesting principal's resolved permissions *before* ranking and *before* any content reaches a prompt.
6. **Audit.** Promotion actions and query access are logged per principal.

**Acknowledged utility cost.** Most-restrictive-wins over-restricts: a fact that also appears in a public doc is hidden from people who can only see the public source. This is a deliberate safe default, not an oversight.

**PII / compliance.** Retention policy and optional PII redaction during normalization/extraction live here. **Right-to-deletion** must hard-remove data despite an append-only, content-hashed, embedded store. The mechanism is **crypto-shredding**: raw content and embeddings for the affected `source_versions` are encrypted per-record, and deletion destroys the key, rendering the ciphertext and any derived fingerprints unrecoverable; a **deletion tombstone** records the fingerprints to prevent silent re-ingest, and the deletion cascades to derived `extractions` and `embeddings`.

---

## Query Layer

### 1. Hybrid search (lexical + vector)

Retrieval is **hybrid lexical + semantic**, not pure-vector. Pure-pgvector retrieval reliably under-ranks exact-term queries — a person's name, a product code, a ticket ID, an error string — which are exactly the queries company-knowledge users type ("find the decision about *Project Atlas*").

- **Semantic leg:** pgvector + HNSW over `embeddings`, queries **pinned to one embedding model version**, with a **stated similarity floor** so results aren't a hairball.
- **Lexical leg:** a Postgres full-text-search index — `tsvector` columns with **GIN** indexes — over the searchable content of `normalized_documents` and `extractions` (decision title/text, fact claim, action description). Co-located in the same DB, so this is nearly free infra-wise.
- **Fusion:** the two result sets are combined by **Reciprocal Rank Fusion (RRF)** (or weighted-score fusion) into a single ranking. Both legs are **ACL-filtered to the requesting principal *before* fusion and ranking** — no candidate reaches the fusion step that the principal could not see at the source.

Returns ranked records with tier, source, confidence, and freshness.

### 2. Graph-Augmented Generation (GAG) — Phase 3

Bounded, **typed** traversal, never an unbounded "depth N":

1. Find seed records via ACL-filtered hybrid (lexical + vector) search.
2. Walk **curation edges** (`derived_from`, `supports`, `supersedes`) up to depth N; walk `related_to` (computed) **only at depth 1**; apply a hard node-count / fan-out cap per edge type.
3. **Score-and-truncate before context assembly** — never inject thousands of nodes into a prompt.
4. Rank by tier (Gold > Extracted > Normalized), recency, and temporal validity.
5. Inject with provenance; **disputed records are surfaced with a warning and their conflicting alternatives, never as authoritative.**

GAG with provenance ("this decision was made in [Slack thread], confirmed by [Confluence doc], currently disputed by [new doc]") is the headline graph feature — which is why the Postgres→graph-DB migration trigger is defined concretely below, not deferred to "if traversal matters."

### Embedding upgrades

`embedding_version` per row is necessary but not sufficient:

- The **trigger** is a model upgrade (more frequent and expensive than content edits), not just a content change.
- **Blue/green rebuild:** a fresh per-version index is built offline, then swapped. Old and new vectors are never mixed in one index.
- Queries **pin to one version** at a time; the cost of a full re-embed is explicitly budgeted (see Cost Model) and attributed per tenant.

---

## Tech Stack

| Layer | Choice | Reason |
|-------|--------|--------|
| Store | **PostgreSQL + pgvector** | Transactional store, queue, review state, and vector index in one DB — a major simplification while proving trust. Typed tables + adjacency-list `edges` + recursive CTEs for shallow expansion. |
| Vector search | **pgvector (HNSW)** | Co-located; avoid Pinecone/Qdrant until scale demands. |
| Extraction model | **Claude** — Haiku (`claude-haiku-4-5-20251001`) for simple/entity extraction; **Sonnet** (`claude-sonnet-4-6`) for decisions, policies, conflict, and synthesis | Model tiering controls cost; structured outputs guarantee parseable extraction. |
| Embeddings | **`text-embedding-3-small`** | Cost-efficient at corpus scale. |
| Pipeline | **Python + async workers** | Queue-based; easy to add connectors. |
| Queue | **Postgres `SKIP LOCKED`** + retry/backoff + dead-letter | Zero infra to start. |
| API | **FastAPI** | Familiar stack. |
| Review UI | **React SPA** | Review queue, evidence viewer, sync/job status. |
| Hosting | **Single VPS to start** | Cheap until there's signal. |

**Migration trigger to a graph DB (Neo4j)** is concrete: move when graph-native operations become central UX — deep multi-hop (>2–3 hops) at low latency, path queries ("show the chain of decisions that led to this policy"), graph algorithms (centrality/community/propagation), interactive neighborhood exploration, permission-aware traversal across many tenants, or per-tenant edge counts in the tens/hundreds of millions.

**Queue/OLTP/vector split trigger** is also concrete: a sustained queue-depth backlog, or a p99 GAG query-latency regression caused by extraction-cascade write contention on the shared instance.

---

## Cost Model

**Pricing (public, as of 2026-06-17):** Claude Sonnet 4.6 ≈ `$3 / 1M` input, `$15 / 1M` output. `text-embedding-3-small` ≈ `$0.02 / 1M`.

**Baseline — 1000-page Notion workspace, 10% daily change:**

- 100 changed pages/day; avg normalized page 1,500 input tokens; prompt/schema overhead 700 tokens; avg output 500 tokens; one extraction + one embedding per changed page.
- Per-document extraction: input `2,200 × $3/1M = $0.0066`, output `500 × $15/1M = $0.0075` → **≈ $0.0141/doc**; embedding `1,500 × $0.02/1M ≈ $0.00003`.
- Initial indexing: Claude ≈ **$14**, embeddings ≈ $0.03.
- Steady state: Claude ≈ **$1.41/day** → **≈ $42/month Claude**; embeddings ≈ $0.003/day.

**Realistic range** (workspaces are uneven): small/tight `$25–$50/mo`; medium docs `$60–$100/mo`; long docs with chunking, retries, entity resolution, contradiction checks, and synthesis `$150–$300+/mo`. Separate passes (entities / decisions / conflict / resolution / synthesis) multiply cost 2–5×; synchronous Tier 4 regeneration on every change is unbounded — avoid it. **The biggest hidden cost is repeated extraction + human review time, not embeddings.**

**Cost controls (in the design from day 1):**

- **Batches API** for non-latency-sensitive extraction (≈50% off).
- **Prompt caching** on the large fixed schema/instruction prefix reused on every extraction call.
- **Model tiering** — Haiku for simple/entity extraction, Sonnet reserved for decisions/policies/conflict/synthesis.
- Skip extraction for low-signal pages via a cheap heuristic/classifier.
- Chunk-level hashing — reprocess only changed chunks.
- Cap output length and records-per-chunk.
- Asynchronous, rate-limited Tier 4 (never synchronous per change); a spend cap / rate limiter on re-derivation storms (one popular doc → large cascade).
- **Per-tenant / per-source / per-job cost tracking in the database from day 1.**

---

## Evaluation & Quality

The vision's central claim is that the graph gets *more accurate*. That must be measurable:

- **Golden eval set** of documents with hand-labeled extractions; track **extraction precision/recall** per record type and per model/prompt version.
- **Human override rate** (confirm vs. edit vs. reject, from `review_items`) as a continuous quality signal, sliced by source type and `node_type`.
- **Regression detection** when the extraction model or prompt changes — re-run the golden set and block deploy on a precision drop.
- **Contradiction-detector eval** — its own precision/recall, since its false negatives corrupt Gold.
- **Named feedback loop:** confirmed/edited records feed a few-shot example store and prompt iteration; fine-tuning only if/when volume justifies it.

The metrics above are all *intrinsic* — they measure extraction quality, not whether the graph actually produces better answers. The vision's central claim ("the graph is more useful") needs a **head-to-head A/B track** alongside them:

- **Conditions:** graph-augmented generation (**GAG**) vs. flat semantic RAG, answering the **same question set** over the same corpus.
- **Scoring:** a **blind LLM-as-judge** grades answers without knowing which condition produced them.
- **Tracked deltas:** answer-quality delta, token-usage delta, and tool-call-count delta between the two conditions.
- **Purpose:** prove the graph *earns its added complexity*. If GAG does not measurably beat flat RAG on answer quality (at acceptable token/tool-call cost), the graph machinery is not paying for itself.

---

## Observability

Operational surfaces beyond a daily digest:

- **Pipeline metrics:** queue depth, extraction latency (p50/p99), token usage, parse/validation failure rate, embedding throughput.
- **Backlog metrics:** stale-node backlog size, conflict-queue size, **review SLA** (age of oldest `pending_review` / `disputed`).
- **Connector health:** per-connector sync state, cursor lag, last successful reconciliation, dead-letter count, token-expiry/outage alerts (a wedged connector must not look like "no changes").
- **Cost dashboards:** per-tenant/source/job spend, re-derivation-storm detection.
- **Access audit:** query access and promotion actions per principal.
- **Integrity:** edge-orphan-GC results and referential-invariant violations.

---

## Phase Plan

### Phase 1 — Prove trustworthy, incremental, source-backed extraction
**Estimated effort: 8–12 weeks for one experienced engineer** (≈5–7 weeks for two disciplined engineers avoiding graph-viz/synthesis distractions; a faked demo is 1–2 weeks but won't earn trust). Phase 1 contains four independently substantial subsystems — connector-with-reconciliation, incremental cascade, ACL propagation+enforcement, and validated extraction — so entity resolution, the eval harness, and ACL group resolution ship *thin* in Phase 1 and are hardened in Phase 2.

- One connector: **Notion** (OAuth/token, block-tree fetch, block→normalized-text with stable offsets, checkpointed sync, content hashing, rate-limit handling, reconciliation, delete/permission-change approximation).
- Normalize + chunk (stable boundaries/offsets, overlap windows) + embed.
- **Extraction with the strict `extraction.v1` schema + required evidence spans**, JSON-Schema/Pydantic validation, repair/retry, offset-first evidence-span verifier, deterministic fingerprints.
- Entity **mention** storage + basic resolution/dedup (thin).
- **Incremental sync from day 1** — delta detection, chunk-level staleness, per-record re-derivation (the differentiator). **No conflict detection yet.**
- **No auto-promotion. Manual Gold curation only** (`trust_state` from the start).
- Typed schema with `tenant_id` / version / `node_type` / `trust_state` from the start; queue with retry/backoff/dead-letter.
- **Access control enforced at query time** (single-connector: captured principal lists) + semantic search over extractions/normalized content.
- Minimal review UI (queue, evidence-next-to-source viewer, edit/confirm/reject, search, sync/job status).
- Observability + **per-tenant cost accounting** from day 1.

### Phase 2 — Conflict handling + safe automation + second source
- **Contradiction detection** as its own classifier step (own confidence/thresholds) + dispute lifecycle + `conflicts` records.
- **Auto-promotion rules** (gated, low-risk facts only).
- Second connector (Slack or GitHub) — lights up **cross-source identity mapping** and **live group-membership resolution** in ACL enforcement.
- **Entity resolution** pass (mention → canonical) hardened, including merge/split.

### Phase 3 — Graph-Augmented Generation
- Bounded typed traversal, score-and-truncate context assembly.
- Provenance in responses, dispute-aware answers.
- Tier 4 synthesis (async, rate-limited, temporal-validity-aware).

### Phase 4 — Ecosystem
- Connector SDK (3rd-party); webhook real-time support (as a latency optimization over reconciliation).
- External query API.
- Full multi-tenant (columns present from Phase 1; this lights them up, including opt-in shareable/public Gold).

---

## Open Questions

1. **Reviewer authority model** beyond per-topic thresholds — roles/ownership for who may promote to Gold, and how to handle reviewer disagreement (partly addressed by `review_items.reviewer_id` audit trail).
2. **Retention/compliance specifics per deployment** — data residency, retention windows. Policy hooks and the crypto-shredding deletion mechanism exist; per-deployment defaults are TBD.
3. **Calibration of contradiction-detector thresholds** against real review-queue load — to be tuned in Phase 2 with the eval set.
4. **Group-resolution performance** — live query-time group-membership resolution adds latency to every query; the membership-cache TTL must be tuned against the leak-window vs. latency tradeoff.
5. **Public/shared Gold** — when cross-tenant or public knowledge sharing lights up in Phase 4, the most-restrictive-wins default needs an explicit opt-in override path without reopening leak risk.
