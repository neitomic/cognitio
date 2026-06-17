# Cognitio — Design Document

> **Revision note (2026-06-17).** This is a substantial rewrite of the original design (archived as `DESIGN-v1.md`) incorporating two critique reviews: an architecture critique (`REVIEW-cc.md`) and an implementation critique (`REVIEW-codex.md`). The headline changes: first-class **access control**, an explicit **conflict/dispute lifecycle**, a **typed relational data model** (replacing the generic `Node` table), **strict evidence-bearing extraction schemas**, **tightened auto-promotion**, **incremental sync from day 1**, and added sections on **security, evaluation, observability, entity resolution, and cost**. Phases were re-cut around the incremental-indexing hypothesis.

---

## Vision

A living knowledge platform for companies. Unlike static RAG systems that index documents once and drift out of date, Cognitio maintains a continuously updated, tiered knowledge graph where content from documents, discussions, and comments flows in incrementally and is progressively distilled into higher-quality, structured knowledge.

The model is a **collaborative pair**: AI proposes enrichments and connections; humans confirm or override high-stakes ones. Over time the graph becomes more accurate, more connected, and more useful — not less, as it would with a stale index.

**What this is, honestly.** The first version is a *source-backed extraction and review system with semantic search*, not a grand graph platform. The hard, differentiating work is building trustworthy extraction records with exact provenance, keeping connectors synchronized despite messy source APIs, and making review efficient enough that "Gold" knowledge means something. The graph becomes richer *after* users trust the extracted knowledge — not before.

---

## Core Concept: Tiered Knowledge

Knowledge in Cognitio is organized into **tiers** that reflect how processed and trusted it is. Tiers are a conceptual ladder, but — critically — they are **not** one polymorphic table (see Data Model). Each tier maps to typed, purpose-built tables.

### Tiers

| Tier | Name | Description |
|------|------|-------------|
| 0 | **Raw** | Immutable raw snapshots from source systems (a Notion page, a Slack thread). Changes create a new version, never an in-place edit. Stored in `source_versions`. |
| 1 | **Normalized** | Cleaned, normalized text with **stable chunk boundaries and character offsets**, metadata extracted, language detected. Still 1:1 with a source version. Stored in `normalized_documents`. |
| 2 | **Extracted** | Typed records pulled out by the model — decisions, actions, facts, entities, open questions — **each carrying required evidence spans (character offsets into the normalized text)**. Stored in `extractions`. This is *source-backed extracted knowledge*, not yet authoritative. |
| 3 | **Gold** | Curated, authoritative knowledge. Reached only by human confirmation, or (Phase 2+) by *narrowly* scoped auto-promotion of low-risk facts (see Auto-Promotion Rules). |
| 4 | **Synthesized** | Cross-source summaries and derived insights spanning multiple Gold records. Expensive; deferred to Phase 3. |

Tier 0/1 normalization is cheap and can be a single pipeline step; the two are kept as distinct rows because chunk offsets (Tier 1) must be stable and independent of raw bytes (Tier 0).

### Edges

Edges are **typed relationships** with their own confidence and provenance (see Data Model `edges`):

- `derived_from` — an extraction derives from a source version / normalized chunk
- `references` — explicit mention (parsed @-mention, link)
- `supersedes` — a newer record replaces an older one (cross-record replacement only, **not** intra-record history — that's handled by version fields)
- `supports` / `contradicts` — two records agree or conflict; **inferred, so they carry their own confidence** and provenance
- `related_to` — semantic similarity. **Not materialized in Phase 1** — computed at query time from embeddings (see below)

**`related_to` is computed, not stored, in Phase 1.** Similarity edges are noisy, high-volume, and model/version-dependent. Persisting them eagerly produces a hairball that makes any graph traversal meaningless and pins the graph to one embedding model. We compute semantic neighbors at query time from the `embeddings` table, with a stated similarity floor. We only materialize `related_to` later if a concrete latency/UX need is measured.

---

## Security & Access Control (first-class, from Phase 1)

This is a **showstopper for company knowledge** and is designed in from day 1, not bolted on.

Source systems (Notion, Slack, Drive) have per-object ACLs. Cognitio must never let a query surface a fact a user could not see at the source — including facts that flow into synthesized/derived records.

**Design:**

1. **Ingest ACLs as node metadata.** Every `source_version` records the source object's access control descriptor (principals/groups allowed, visibility scope) captured at fetch time. Permission changes are *changes* and trigger re-fetch even when content is unchanged (connectors expose this via capabilities).
2. **Propagate restrictions through derivation.** Every `extraction` records the set of source versions it derives from. Its effective ACL is the **union of source restrictions** (most-restrictive-wins): a derived/synthesized record is visible only to principals who could see **all** of its sources.
3. **Enforce at query time.** Search and graph traversal filter candidates by the requesting principal's permissions *before* ranking and *before* any content reaches a prompt. A synthesized Tier 4 node carries the intersection of viewer sets of its constituent Gold records.
4. **Audit.** Reviewer/promotion actions and query access are logged per principal (see `review_items` audit trail and Observability).

PII / compliance are tracked here too: retention policy, right-to-deletion handling (which interacts with "immutable Tier 0" and deletion semantics — see Deletion below), and optional PII redaction during normalization/extraction.

---

## Status: Three Orthogonal Axes

The original single `status` enum (`active | stale | archived | pending_review`) conflated unrelated concepts and admitted illegal combinations. It is split into three independent columns, present on the relevant records:

| Axis | Field | Values | Meaning |
|------|-------|--------|---------|
| **Lifecycle** | `lifecycle` | `active` \| `archived` | Is this record part of the live knowledge base? |
| **Freshness** | `freshness` | `current` \| `stale` | Does this reflect the latest source version, or is it queued for re-derivation? |
| **Workflow** | `workflow` | `none` \| `pending_review` \| `disputed` | Where is this in the human/conflict workflow? |

A record can legitimately be `active` + `stale` + `pending_review` simultaneously — three axes, no illegal states.

---

## Conflict & Dispute Lifecycle

Contradiction handling is engineered, not assumed.

### Contradiction detection is its own step

"Contradicts" between two natural-language facts is an **LLM/NLI classification task with its own error rate** — not a primitive. It is a distinct pipeline step (Phase 2) that:

- runs a dedicated classifier (NLI model or LLM with a strict prompt) over candidate fact pairs surfaced by semantic similarity + shared subject entities,
- emits its **own confidence score**, separate from extraction confidence,
- has explicit thresholds and a stated policy for *its* false positives (flood the review queue) and false negatives (silently corrupt Gold). High-confidence contradictions auto-open a dispute; borderline ones are sampled into eval.

### `disputed` status and query behavior

When a re-derived fact contradicts existing Gold:

- the contradicted Gold record's `workflow` becomes **`disputed`** (it is **not** silently overwritten and **not** demoted out of the graph),
- a **`Conflict` record** groups the involved records, the `contradicts` edges, the detector's confidence, and (when proposed) a resolution. Conflicts are a first-class resolution unit, not loose pairwise edges — this handles multi-way conflicts and transitivity (three sources disagree; two new facts contradict different Golds) as one consistent set,
- **query-time behavior:** a `disputed` record is **still returned, but with a warning and explicitly not as authoritative.** GAG includes the conflicting alternatives and the open dispute in provenance rather than asserting one side. The query layer has first-class knowledge of dispute state.

A reviewer resolves the whole conflict set at once (pick a winner, mark both time-scoped, supersede, etc.), which clears `disputed` consistently across the set.

---

## Incremental Indexing (the differentiator — Phase 1)

When a source changes:

1. **Delta detection.** Connectors return changes via cursor-based, capability-aware scans (see Connectors). **Chunk-level hashing:** `normalized_documents` store per-chunk boundaries and per-chunk hashes, so only changed chunks are reprocessed — not the whole document. A whole-document hash only tells you *that* something changed, not *which chunk*; chunk hashes make the "cheap targeted update" claim real.
2. **Monotonic write ordering.** Each `source_item` tracks a monotonic source revision/`updated_at`. Writes that would regress it are rejected, preventing out-of-order `fetch` results (retries, parallel workers) from overwriting newer content.
3. **Invalidation propagation.** Extractions derived from a changed chunk are flagged `freshness = stale` **per-record** and queued for re-derivation. Staleness is a per-node flag cleared only when *that* node's re-derivation commits — so a crashed cascade is **resumable**, not all-or-nothing.
4. **Re-derivation.** The model re-extracts from updated chunks. New extractions are compared to prior ones via deterministic fingerprints (below): unchanged facts kept, changed facts versioned, new facts added.
5. **Conflict detection (Phase 2).** A re-derived fact that contradicts existing Gold opens a dispute (above) rather than overwriting.
6. **Downstream cascade (Phase 3).** Synthesized nodes depending on changed Gold are re-queued — asynchronously and rate-limited, never synchronously per change.

**Idempotency.** Every extracted record gets a deterministic fingerprint `hash(type + normalized_claim + evidence_span + source_version_id)`. Re-running a derivation is a no-op if the fingerprint already exists. Cascade steps are individually tracked and idempotent.

**Connector health & reconciliation.** Webhooks are at-least-once and lossy; they are treated as a *latency optimization only*. Periodic `incremental_scan` / full reconciliation is the source of truth. Connectors track sync state, high-watermark cursor, and health; a wedged connector (token expiry, outage) is distinguishable from "no changes" and alerts (see Observability).

**Deletion.** `change_type: deleted` (discovered via `tombstone_scan`) does **not** trigger re-derivation (impossible — content is gone). Instead derived records are marked `lifecycle = archived` but retained (a deleted source doc may still contain valid facts); right-to-deletion requests force hard removal across the derivation chain.

---

## Source Connectors

The clean `list_changes(since)` interface is insufficient for real SaaS APIs (timestamps non-monotonic/rounded/timezone-weird; pagination reorders; some APIs lack delta queries; deletions hard to discover; permission changes are content-invisible changes). The contract is **cursor-based and capability-aware**:

```python
class Connector:
    def capabilities(self) -> ConnectorCapabilities: ...
    async def full_scan(self, cursor: str | None) -> Page[SourceRef]: ...
    async def incremental_scan(self, cursor: str | None) -> Page[ChangeEvent]: ...
    async def fetch(self, ref: SourceRef) -> SourceSnapshot: ...
    async def fetch_children(self, ref: SourceRef, cursor: str | None) -> Page[SourceRef]: ...
    async def tombstone_scan(self, cursor: str | None) -> Page[Tombstone]: ...
```

Each `Page` returns `items`, `next_cursor`, `high_watermark`, `sync_started_at`, `has_more`, `retry_after`. **Capabilities** declare: incremental cursor support, updated-since filter, webhooks, tombstones, permission metadata, child expansion, stable content hashes. Sources without delta queries fall back to periodic full scans with content hashing (acceptable for small workspaces; must be scoped by database/channel/folder for large ones).

Per-connector sync strategies differ (Notion block-tree + `last_edited_time` + reconciliation; Slack history+replies + edit/delete events; Drive cursor-based changes API; GitHub webhooks + updated cursors; Confluence version APIs + explicit comment/attachment/permission handling).

**From day 1, every connector needs:** stored sync checkpoint, idempotent change events, content hashes to skip no-op fetches, retry/backoff + dead-letter state, periodic reconciliation, and tombstone/permission-loss handling. Without these the *first* connector drifts.

**Phase 1 connector:** Notion only.

---

## AI Extraction Pipeline

Model output is **untrusted until schema validation and evidence-span verification pass.** Extraction uses Claude **structured outputs** (`output_config.format`) to guarantee parseable JSON — never free-text parsing.

### Structured output schema (`extraction.v1`)

One response envelope per normalized document/chunk. **`evidence_spans` are required for every extracted record** — character offsets into the *exact normalized text version*, not the mutable source.

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

**Implementation rules:**

- Require `evidence_spans` for every extraction; **reject records whose evidence text does not match the source span.**
- Offsets are against the exact normalized text version (immutable), not the source.
- `local_id`s are scoped to one model response; map to durable DB IDs only after validation.
- Deterministic fingerprint `hash(type + normalized_claim + evidence_span + source_version_id)` for idempotency.
- Validate every response with JSON Schema / Pydantic before any write; malformed JSON goes through a repair/retry path.
- **Entity mentions are separate from canonical entities** — extraction produces `entity_mentions`; resolution to `entities` is a separate pass (see Entity Resolution).
- Per-extraction-type confidence (the model produces one per record), not a single node-level float.

### Known extraction failure modes (must be handled explicitly)

Decisions vs. proposals ("we should" ≠ "we decided"); relative dates needing source timestamp + timezone; implicit owners ("Alice can take this" may be a suggestion); pronouns/missing thread context; duplicate facts across sources; entity ambiguity ("Platform", "Core", first names); stale source truth; permission leaks via summaries; table/list/row semantics in Notion DBs and Sheets; low-signal extraction spam; over-normalization erasing operative wording; chunk-boundary context loss.

### Human review flow

Reviewers see a queue of extracted records grouped by topic, each shown **next to its source evidence**. One-click confirm → Gold; edit + confirm → corrected Gold (override captured as eval signal); reject → discarded as negative example. Every action writes to the `review_items` audit trail (which reviewer, when, what changed).

---

## Auto-Promotion Rules (tightened)

Model confidence is **not calibrated** and must not, on its own, carry trust. A model is routinely *confidently wrong* about owners, dates, and decision-vs-proposal.

**Phase 1: no auto-promotion at all.** Gold is reached only by human confirmation. The MVP optimizes for trustworthy, evidence-first review.

**Phase 2+: narrow auto-promotion of low-risk simple facts only.** Decisions, policies, ownership, deadlines, and customer commitments are **never** auto-promoted on confidence alone. To auto-promote, a record must satisfy **all** of:

- confidence ≥ 0.9, **and**
- has an exact, verified evidence span, **and**
- comes from a source type **allowed to be authoritative** (not, e.g., an untrusted comment), **and**
- has **no unresolved pronouns or relative dates**, **and**
- has **no conflict** with existing Gold (requires conflict detection — hence Phase 2), **and**
- passes deterministic validation.

Only `facts` of low-risk `claim_type` (e.g. `state`, simple `metric`) qualify. Everything else routes to human review. Even auto-promoted records are labeled *source-backed extracted knowledge* unless a human or an authoritative source elevates them.

| Level | Trigger | Action |
|-------|---------|--------|
| **Auto-promote** (Phase 2+) | All gates above pass, low-risk fact only | Extracted → Gold, logged with full provenance |
| **Soft review** | Plausible but not gate-passing | `workflow = pending_review`, shown in queue |
| **Hard gate** | Low confidence, high-risk type, or conflict | Stays Extracted, blocks until human acts |

---

## Entity Resolution

A named, separate component — not absorbed into `related_to` similarity.

The same decision discussed in Slack, written up in Confluence, and recapped in a meeting note produces multiple raw nodes and near-identical extractions. Without canonicalization, Gold accumulates duplicates and Tier 4 synthesis double-counts ("14 discussions" may be 6 distinct decisions echoed).

**Two-pass design:**

1. **Mention pass (extraction time).** Every entity reference becomes an `entity_mention` row with its span and surface form. Cheap, runs on the extraction model.
2. **Resolution pass (separate, async).** Mentions are clustered to canonical `entities` via blocking (name/alias normalization) + embedding similarity + a resolution model for ambiguous cases. Resolution is revisable and produces an audit trail; merging/splitting entities is a first-class operation. Fact/decision dedup keys off resolved entities + fingerprints.

---

## Data Model (typed relational schema)

The generic polymorphic `Node` table is **replaced** with purpose-built tables. Core fields (extraction type, evidence span, action owner, due date, decision status, review state, ACL) are **first-class columns or schema-validated typed JSON**, not freeform `jsonb`. `metadata: jsonb` remains only for connector-specific fields.

**Present on every row from day 1:** `tenant_id`, version fields (`version` + `is_current`, indexed), and `node_type` as a first-class column where applicable. These are cheap now and painful migrations later.

```
source_items {
  id, tenant_id, node_type,
  connector, source_id,            -- stable external id
  source_url, current_version_id,
  source_revision,                 -- monotonic; writes can't regress it
  acl,                             -- captured access descriptor
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

extractions {                      -- Tier 2: typed extracted records
  id, tenant_id, node_type,        -- decision | action | fact | entity_ref | open_question
  source_version_id, normalized_document_id, chunk_id,
  payload,                         -- schema-validated typed JSON (extraction.v1)
  evidence_spans: [ {start_char,end_char,text} ],   -- REQUIRED
  fingerprint,                     -- hash(type+claim+span+source_version_id)
  confidence,                      -- per-extraction
  effective_acl,                   -- union of source restrictions
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
  canonical_name, aliases, description,
  effective_acl,
  lifecycle, version, is_current, created_at
}

edges {                            -- typed relationships
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
```

Notes:

- **Versioning** is explicit (`version` + indexed `is_current`), so "current only" queries don't traverse `supersedes` chains; `supersedes` is reserved for cross-record replacement.
- **Embeddings are version-aware in their own table.** Mixed-version vectors are incomparable in one ANN index (HNSW indexes one space), so re-embedding with a new model uses a **blue/green index rebuild** and queries **pin to one embedding version at a time** (see Embedding Upgrades).
- **Temporal validity** (`valid_from`/`valid_to` on facts/edges) lets synthesis avoid mixing expired and current claims ("deprecate v1 by Q3" is true until Q3).

---

## Query Layer

### 1. Semantic search

pgvector + HNSW over `embeddings`, queries **pinned to one embedding model version**, with a **stated similarity floor** so results aren't a hairball. Returns ranked records with tier, source, confidence, freshness, and — always — **ACL-filtered to the requesting principal before ranking.**

### 2. Graph-Augmented Generation (GAG) — Phase 3

Bounded, **typed** traversal (not unbounded "depth N"):

1. Find seed records via ACL-filtered semantic search.
2. Walk **curation edges** (`derived_from`, `supports`, `supersedes`) up to depth N; walk `related_to` (computed) **only at depth 1**; hard node-count / fan-out cap per edge type.
3. **Score-and-truncate before context assembly** — never inject thousands of nodes into a prompt.
4. Rank by tier (Gold > Extracted > Normalized), recency, and temporal validity.
5. Inject with provenance; **disputed records are surfaced with a warning and their conflicting alternatives, never as authoritative.**

GAG with provenance ("this decision was made in [Slack thread], confirmed by [Confluence doc], currently disputed by [new doc]") is the headline graph feature — which is exactly why the Postgres→graph-DB migration trigger is defined concretely (below), not deferred to "if traversal matters."

---

## Embedding Upgrades

`embedding_version` per row is necessary but not sufficient. The upgrade procedure:

- **Trigger** is a model upgrade (more frequent and expensive than content edits), not just content change.
- **Blue/green rebuild:** build a fresh per-version index offline, then swap. Old and new vectors are never mixed in one index.
- Queries **pin to one version** at a time; the cost of a full re-embed is explicitly budgeted (see Cost) and attributed per tenant.

---

## Tech Stack

| Layer | Choice | Reason |
|-------|--------|--------|
| Store | **PostgreSQL + pgvector** | Transactional store, queue, review state, and vector index in one DB — a major simplification while proving trust. Typed tables + adjacency-list `edges` + recursive CTEs for shallow expansion. |
| Vector search | **pgvector (HNSW)** | Co-located; avoid Pinecone/Qdrant until scale demands. |
| Extraction model | **Claude** — Haiku (`claude-haiku-4-5-20251001`) for simple/entity extraction, **Sonnet** (`claude-sonnet-4-6`) for decisions/policies/conflict/synthesis | Model tiering controls cost; structured outputs guarantee parseable extraction. |
| Embeddings | **`text-embedding-3-small`** | Cost-efficient at corpus scale. |
| Pipeline | **Python + async workers** | Queue-based, easy to add connectors. |
| Queue | **Postgres `SKIP LOCKED`** + retry/backoff + dead-letter | Zero infra to start. |
| API | **FastAPI** | Familiar stack. |
| Review UI | **React SPA** | Review queue, evidence viewer, sync/job status. |
| Hosting | **Single VPS to start** | Cheap until there's signal. |

**Migration trigger to a graph DB (Neo4j) is concrete, not "if traversal matters":** move when graph-native operations become central UX — deep multi-hop (>2–3 hops) at low latency, path queries, graph algorithms (centrality/community/propagation), interactive neighborhood exploration, permission-aware traversal across many tenants, or per-tenant edge counts in the tens/hundreds of millions. **Queue/OLTP/vector split trigger:** name the signal — sustained queue depth backlog or p99 GAG query latency regression caused by extraction-cascade write contention on the shared instance.

---

## Cost

**Pricing (public, as of 2026-06-17):** Claude Sonnet 4.6 ≈ `$3 / 1M` input, `$15 / 1M` output. `text-embedding-3-small` ≈ `$0.02 / 1M`.

**Baseline — 1000-page Notion workspace, 10% daily change:**

- 100 changed pages/day; avg normalized page 1,500 input tokens; prompt/schema overhead 700 tokens; avg output 500 tokens; one extraction + one embedding per changed page.
- Per-document extraction: input `2,200 × $3/1M = $0.0066`, output `500 × $15/1M = $0.0075` → **≈ $0.0141/doc**; embedding `1,500 × $0.02/1M ≈ $0.00003`.
- Initial indexing: Claude ≈ **$14**, embeddings ≈ $0.03.
- Steady state: Claude ≈ **$1.41/day** → **≈ $42/month Claude**; embeddings ≈ $0.003/day.

**Realistic range** (workspaces are uneven): small/tight `$25–$50/mo`; medium docs `$60–$100/mo`; long docs with chunking, retries, entity resolution, contradiction checks, and synthesis `$150–$300+/mo`. Separate passes (entities / decisions / conflict / resolution / synthesis) multiply cost 2–5×; synchronous Tier 4 regeneration on every change is unbounded — avoid it. **The biggest hidden cost is repeated extraction + human review time, not embeddings.**

**Cost controls (in the design, from day 1):**

- **Batches API** for non-latency-sensitive extraction (≈50% off).
- **Prompt caching** on the large fixed schema/instruction prefix reused on every extraction call.
- **Model tiering** — Haiku for simple/entity extraction, Sonnet reserved for decisions/policies/conflict/synthesis.
- Skip extraction for low-signal pages via cheap heuristic/classifier.
- Chunk-level hashing — reprocess only changed chunks.
- Cap output length and records-per-chunk.
- Asynchronous, rate-limited Tier 4 (never synchronous per change); spend cap / rate limiter on re-derivation storms (one popular doc → large cascade).
- **Per-tenant / per-source / per-job cost tracking in the database from day 1.**

---

## Evaluation & Quality Measurement

The vision's central claim is that the graph gets *more accurate*. That must be measurable:

- **Golden eval set** of documents with hand-labeled extractions; track **extraction precision/recall** per record type and per model/prompt version.
- **Human override rate** (confirm vs. edit vs. reject from `review_items`) as a continuous quality signal, sliced by source type and `node_type`.
- **Regression detection** when the extraction model or prompt changes — re-run the golden set, block deploy on precision drop.
- **Contradiction-detector eval** — its own precision/recall, since its false negatives corrupt Gold.
- **Feedback loop mechanism is named, not vague:** confirmed/edited records feed a few-shot example store and prompt iteration (fine-tuning only if/when volume justifies it).

---

## Observability

Operational surfaces beyond the daily digest:

- **Pipeline metrics:** queue depth, extraction latency (p50/p99), token usage, parse/validation failure rate, embedding throughput.
- **Backlog metrics:** stale-node backlog size, conflict-queue size, **review SLA** (age of oldest `pending_review` / `disputed`).
- **Connector health:** per-connector sync state, cursor lag, last successful reconciliation, dead-letter count, token-expiry/outage alerts (a wedged connector must not look like "no changes").
- **Cost dashboards:** per-tenant/source/job spend, re-derivation-storm detection.
- **Access audit:** query access and promotion actions per principal.

---

## Phase Plan (re-cut around the incremental hypothesis)

### Phase 1 — Prove trustworthy, incremental, source-backed extraction
**Estimated effort: 5–8 weeks for one experienced engineer** (≈3–5 weeks for two disciplined engineers avoiding graph-viz/synthesis distractions; a faked demo is 1–2 weeks but won't earn trust).

- One connector: **Notion** (OAuth/token, block-tree fetch, block→normalized-text with stable offsets, checkpointed sync, content hashing, rate-limit handling, reconciliation, delete/permission-change approximation).
- Normalize + chunk (stable boundaries/offsets) + embed.
- **Extraction with the strict `extraction.v1` schema + required evidence spans**, JSON-Schema/Pydantic validation, repair/retry, evidence-span verifier, deterministic fingerprints.
- Entity **mention** storage + basic resolution/dedup.
- **Incremental sync from day 1** — delta detection, chunk-level staleness, per-record re-derivation (the differentiator). **No conflict detection yet.**
- **No auto-promotion. Manual Gold curation only.**
- Typed schema with `tenant_id` / version / `node_type` from the start; queue with retry/backoff/dead-letter.
- **Access control enforced at query time** + semantic search over extractions/normalized content.
- Minimal review UI (queue, evidence-next-to-source viewer, edit/confirm/reject, search, sync/job status).
- Observability + **per-tenant cost accounting** from day 1.

### Phase 2 — Conflict handling + safe automation + second source
- **Contradiction detection** as its own classifier step (own confidence/thresholds) + dispute lifecycle + `conflicts` records.
- **Auto-promotion rules** (gated, low-risk facts only).
- Second connector (Slack or GitHub).
- **Entity resolution** pass (mention → canonical) hardened.

### Phase 3 — Graph-Augmented Generation
- Bounded typed traversal, score-and-truncate context assembly.
- Provenance in responses, dispute-aware answers.
- Tier 4 synthesis (async, rate-limited, temporal-validity-aware).

### Phase 4 — Ecosystem
- Connector SDK (3rd-party), webhook real-time support (as latency optimization over reconciliation).
- External query API.
- Full multi-tenant (columns present from Phase 1; this lights them up, incl. opt-in shareable/public Gold).

---

## Resolved & Remaining Questions

Several original "open questions" are now decided in the body above:

- **Graph schema flexibility** → typed tables + `node_type` first-class + schema-versioned typed payloads (not "probably").
- **Deletion handling** → archive-but-retain derived facts; hard-delete only for right-to-deletion (see Incremental Indexing / Security).
- **Multi-tenancy** → `tenant_id` on every row from Phase 1; cross-tenant sharing opt-in in Phase 4.
- **Embedding freshness** → version-aware embeddings table + blue/green rebuild + query-time version pinning.
- **Cost model** → concrete estimate + controls (see Cost).

**Still open:**

1. Reviewer authority model beyond per-topic thresholds — roles/ownership for who may promote to Gold, and handling reviewer disagreement (partly addressed by `review_items.reviewer_id` audit trail).
2. Retention/compliance specifics per deployment (residency, retention windows) — policy hooks exist; defaults TBD.
3. Calibration of contradiction-detector thresholds against real review-queue load — to be tuned in Phase 2 with the eval set.
