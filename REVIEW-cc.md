# Design Review — Cognitio

A critique of `DESIGN.md`. Organized by the five areas requested, with a final section on cross-cutting concerns. References are to section headings in the design doc.

The core idea — a tiered, continuously-distilled knowledge graph with human-in-the-loop promotion — is sound and well-motivated. The vision section correctly identifies the failure mode of static RAG. But the document describes a *happy path* and leaves most of the hard distributed-systems and ML-lifecycle problems either unspecified or relegated to "Open Questions." Below is where it will break and what needs to change.

---

## 1. Architecture gaps

### 1.1 Conflict resolution is named but not designed (§Incremental Indexing step 4, §Human-in-the-Loop)
The doc says a re-derived fact that "contradicts an existing gold node" gets flagged for review. The hard parts are undefined:

- **How is "contradicts" detected?** Contradiction detection between two natural-language facts is itself an LLM/NLI task with its own error rate. The doc treats it as a primitive. You need an explicit `contradicts` classifier step, its own confidence score, and a policy for *its* false positives/negatives. A missed contradiction silently corrupts Gold; a spurious one floods the review queue.
- **What is the state of the Gold node while a conflict is pending?** If a Gold fact is contradicted by new evidence, does it stay authoritative (serving stale/wrong answers to queries) or get demoted to `needs_verification` until a human rules? The query layer (§Query Layer) has no notion of "this Gold node is currently disputed," so it will keep returning a contradicted fact as authoritative. This is the single most important state to model and it's absent from the `status` enum.
- **Multi-way conflicts and transitivity.** Three sources disagree; two new facts each contradict different existing Golds. There's no notion of a "conflict set" or resolution unit — only pairwise `contradicts` edges. A reviewer resolving one edge may leave the set inconsistent.

**Change:** Add an explicit conflict lifecycle (a `disputed` status or a first-class `Conflict` entity grouping the nodes + edges + proposed resolution), define the classifier and its thresholds, and specify query-time behavior for disputed nodes.

### 1.2 Graph traversal cost is hand-waved (§Query Layer / GAG)
"Walk edges … up to depth N" on a richly-connected graph is a combinatorial explosion. `related_to` is vector-similarity-based (§Edges), so the graph is *dense* — every node potentially links to dozens of semantically-near neighbors. Depth-2 from a popular seed node can pull thousands of nodes. The doc gives no:

- traversal budget / fan-out cap per edge type,
- edge-type weighting in the walk (you almost certainly want `derived_from`/`supports` traversal to be cheap and `related_to` to be sharply limited or excluded from multi-hop),
- ranking-then-pruning strategy before injecting into context (you can't put thousands of nodes in a prompt).

**Change:** Specify a bounded, typed traversal (e.g., depth-N only along curation edges, `related_to` only at depth 1, hard node-count cap, score-and-truncate before context assembly). Note also that this is the actual argument *for* Neo4j over Postgres (§Tech Stack) — but the doc defers that decision to "if traversal performance matters," when traversal *is* the headline feature (GAG). That ordering is backwards.

### 1.3 Embedding drift / model versioning (Open Question #4 — under-scoped)
Tracking `embedding_version` per node is necessary but not sufficient:

- **Mixed-version ANN search is incoherent.** HNSW (§Query Layer) indexes vectors in one space. If you re-embed with a new model, old and new vectors are not comparable; a single index containing both returns garbage distances. You need either a full re-embed-then-swap (expensive, and the cost is unaccounted for) or per-version indexes with query-time routing. Neither is described.
- **No re-embedding trigger or backfill plan.** "Recompute when content changes" handles content edits but not *model upgrades*, which are the more expensive and more likely event.

**Change:** State the embedding-upgrade procedure (blue/green index rebuild), who pays for it, and that queries pin to one embedding version at a time.

### 1.4 The queue-on-Postgres choice will collide with the read path (§Tech Stack)
`SELECT ... FOR UPDATE SKIP LOCKED` as the work queue, co-located with the graph store and pgvector, on a single VPS (§Tech Stack, §Phase). Extraction is bursty and write-heavy (re-derivation cascades, §Incremental Indexing step 5); GAG queries are read-heavy and traversal-heavy. They contend for the same instance. This is fine for Phase 1 but the doc presents it as a default to grow into, with no stated trigger for splitting queue/OLTP/vector concerns. Name the signal (queue depth, p99 query latency) that forces separation.

---

## 2. Data model weaknesses

### 2.1 The `Node` schema conflates five very different things (§Data Model)
A Tier 0 raw Slack thread and a Tier 4 synthesized trend analysis share one table with one `content: text` field. Problems:

- **No `node_type`** on the Node despite Open Question #1 acknowledging it's needed. Without it, Tier 2 extractions (`Decision`, `Action`, `Entity`, `Claim`, `Question` — listed in §Tiers and §AI Extraction Pipeline) are untyped blobs. You cannot query "all open action items with owners" because owner/assignee/due-date have nowhere to live except `metadata` jsonb with no schema. The extraction pipeline produces structured facts and then throws the structure away into freeform text + untyped jsonb.

**Change:** Promote `node_type` to a first-class column now (not "probably," as Open Q #1 hedges), with a per-type schema for `properties`. This is foundational, not a Phase-4 nicety.

### 2.2 Versioning is asserted but not modeled (§Tiers Tier 0, §Incremental Indexing step 3)
"Immutable — changes to source create a new version, not an in-place edit" and "changed facts are versioned" — but the `Node` schema has no `version`, `previous_version_id`, `valid_from`/`valid_to`, or `is_current` field. `supersedes` is an edge type, which is one way to do it, but then:

- there's no way to query "current version only" efficiently (every query must traverse `supersedes` chains),
- `status: archived` overlaps ambiguously with being superseded,
- the embedding/index must exclude superseded nodes or they pollute search results — unspecified.

**Change:** Add explicit version fields and an `is_current` flag (indexed); reserve `supersedes` for cross-node replacement, not intra-node history.

### 2.3 Edges are under-powered (§Edges, §Data Model Edge)
- **No `confidence` on edges**, only on nodes. But `contradicts`, `supports`, and `related_to` are all *inferred* (by model or by vector similarity) and need their own confidence — especially `contradicts`, which gates human review.
- **No provenance distinguishing edge creation method** beyond `created_by: human | model`. A `related_to` from cosine similarity, a `references` from explicit @-mention parsing, and a `supports` from LLM inference are wildly different in trustworthiness but indistinguishable here.
- **No temporal validity on facts.** "We deprecate v1 by Q3" is true until Q3. The model has `created_at`/`indexed_at` (ingestion time) but no notion of the *fact's* validity window. Synthesized trends (§Tiers Tier 4) over time-sensitive facts will silently mix expired and current claims.

### 2.4 Multi-tenancy is bolted on later but touches every row (§Phase 4, Open Q #3)
There is no `tenant_id` / `workspace_id` on Node, Edge, or ChangeEvent. Retrofitting tenancy into a graph (especially shared/public nodes, Open Q #3) after Phase 1 data exists is a painful migration. Even if multi-tenant ships in Phase 4, add the column in Phase 1.

---

## 3. Incremental indexing edge cases

This section (§Incremental Indexing, §Source Connectors, §Data Model ChangeEvent) is the stated differentiator and has the most unhandled failure modes.

### 3.1 Out-of-order and concurrent events
`list_changes(since: timestamp)` is timestamp-based delta fetch. Failure modes not addressed:

- **Clock skew / equal timestamps** across source items → the `since` cursor can skip or double-process events. Cursor-based (opaque continuation token) pagination is safer than wall-clock `since`, where the connector supports it.
- **Two edits to the same source between polls** → only the latest content is fetched (fine), but if events are processed out of order (retries, parallel workers), an older `fetch_content` result can overwrite a newer one. There is no per-source-id sequencing or version check on write. `content_hash` lets you detect *no-op* changes but not *ordering*.

**Change:** Add a monotonic per-source sequence/version (source `updated_at` or revision id) and reject writes that would regress it.

### 3.2 Partial-failure mid-cascade
The cascade is: invalidate Tier 2+ → re-derive → conflict-check → re-synthesize (§Incremental Indexing steps 2–5). What happens when re-derivation succeeds but synthesis fails, or the worker dies mid-cascade? `ChangeEvent.status` is a single `pending|processing|done|failed` for the *whole* change — too coarse. A node can be left `stale` forever if the cascade partially completes and the event is marked `done`, or re-done wastefully if marked `failed`. There's no idempotency key on the derivation step.

**Change:** Make the cascade steps individually tracked and idempotent (e.g., per-node `stale` flags that are cleared only when *that* node's re-derivation commits), so a crashed cascade is resumable rather than all-or-nothing.

### 3.3 Connector downtime and backfill
- **Webhook gaps (§Source Connectors `subscribe`)**: push events are lossy (network blips, endpoint downtime, no replay — exactly the SSE-style problem). The doc lists `subscribe` as "optional push-based" with no reconciliation against the pull path. You need periodic `list_changes` reconciliation as the source of truth and treat webhooks as a latency optimization only.
- **Source outage / token expiry**: `list_changes` failing for hours means the `since` cursor stays put — fine — but the doc has no connector health/state tracking, no alerting, and no backoff coordination (retry/backoff is mentioned only in Phase 2). A connector silently wedged is indistinguishable from "no changes."
- **Deletion semantics (Open Q #2) interact with cascade**: when a source is deleted, derived Gold facts may still be valid. But `change_type: deleted` has no defined propagation rule. Re-deriving from deleted content is impossible, so the invalidation step (which assumes re-derivation) has no path for deletes.

### 3.4 Hash granularity vs. "changed chunks" (§Incremental Indexing step 1)
"Compare new content to last-indexed version (hash or diff). Only changed chunks are reprocessed." But `content_hash` in the schema is a single sha256 over the whole node — that detects *that* something changed, not *which chunk*. Chunk-level delta requires storing chunk boundaries + per-chunk hashes, which isn't in the model. As written, any edit reprocesses the whole node, undercutting the "cheap targeted update" claim (§Incremental Indexing closing line).

---

## 4. Phase sequencing

The phasing (§Phase Plan) is mostly reasonable — one connector, prove extraction, minimal review UI — but it's **mis-scoped in both directions**.

### 4.1 Phase 1 defers the thing that proves the concept
The doc's own thesis is *incremental* indexing (the "key differentiator," §Incremental Indexing). Yet Phase 1 is a one-shot index (connector → normalize → extract → search) and **all** incremental machinery — delta detection, invalidation, conflict detection — is Phase 2. That means Phase 1 is "yet another RAG-over-Notion," which proves nothing the doc claims is novel. At minimum, **delta detection + re-derivation for a single connector belongs in Phase 1**, even without conflict handling, or the MVP doesn't test the core hypothesis.

### 4.2 Phase 1 simultaneously over-builds the tier ladder
Conversely, Tiers 0→1→2 + Gold promotion + semantic search + a review UI is a lot for a concept proof. Consider collapsing: Tier 0/1 can be one step (normalization is cheap and rarely needs to be its own queryable tier early on). Tier 4 (Synthesized) is correctly deferred, good.

### 4.3 Conflict detection is sequenced too late relative to auto-promotion
Auto-promote at confidence ≥ 0.9 (§Human-in-the-Loop) ships conceptually in Phase 1's review flow, but conflict detection is Phase 2. So in Phase 1 you can auto-promote a fact to Gold that contradicts existing Gold, with no detection. Either disable auto-promotion until conflict detection exists, or pull conflict detection forward. Shipping auto-promote without conflict-checking is a correctness bug, not just a missing feature.

**Change:** Re-cut phases around the hypothesis: Phase 1 = one connector + normalize + extract + **delta re-derivation** + search (no auto-promote, manual Gold only). Phase 2 = conflict detection + auto-promote + retry/backoff + second connector.

---

## 5. Missing pieces (not in the doc at all)

1. **Security / access control / permission inheritance.** The biggest omission. Source systems (Notion, Slack, Drive) have per-document ACLs. Cognitio flattens everything into one graph and a query layer that returns Gold facts with provenance — but nothing carries *who is allowed to see this*. A synthesized Tier 4 node can leak a fact derived from a private channel to someone who couldn't see the source. This is a showstopper for "company knowledge" and must be designed from Phase 1: source ACLs need to be ingested as node metadata and enforced at query time, including for synthesized/derived nodes (which inherit the *union* of their sources' restrictions).

2. **Evaluation / quality measurement.** There is no way to know if the graph is "getting more accurate" (the vision's central claim). No golden eval set, no extraction precision/recall tracking, no measurement of human override rate as a quality signal, no regression detection when the extraction model or prompt changes. The `model_proposed` → confirm/edit/reject loop (§AI Extraction) generates training/eval data but the doc only mentions feeding it back "to improve future extractions" with no mechanism (fine-tune? few-shot example store? prompt iteration?).

3. **PII / compliance / data residency / retention.** Company knowledge includes personal data. No retention policy, no right-to-deletion handling (which conflicts with "immutable" Tier 0 and "keep facts even if source deleted," Open Q #2), no PII redaction in extraction.

4. **Cost controls beyond the open question.** Open Q #5 flags cost but proposes nothing. Concretely, the design should specify: use the **Batches API** for non-latency-sensitive extraction (50% cost reduction) and **prompt caching** for the schema-guided extraction prompt (it's a large, fixed prefix reused on every Tier 1→2 call — a textbook caching win). Also reconsider model tiering: §Tech Stack fixes Sonnet for all extraction, but normalization/simple-entity extraction could run on Haiku, reserving a stronger model for low-confidence or conflict cases. A re-derivation storm (one popular doc edited → large cascade) has no spend cap or rate limiter.

5. **Observability of the pipeline itself.** No metrics on queue depth, extraction latency, stale-node backlog, conflict-queue size, or review SLA. The "daily digest" (§Human-in-the-Loop) is the only operational surface mentioned.

6. **Idempotency and dedupe of nodes across sources.** The same decision discussed in Slack, then written up in Confluence, produces two raw nodes and likely two near-identical extracted facts. There's no entity/fact resolution (canonicalization) strategy beyond `related_to` similarity — so Gold accumulates duplicates, and synthesis (Tier 4) double-counts ("14 discussions" may be 6 distinct decisions echoed). Entity resolution deserves to be a named component.

7. **Reviewer scaling / trust model.** Who can promote to Gold? Per-topic thresholds (§Human-in-the-Loop) imply roles/ownership, but there's no model for reviewer authority, audit trail of who promoted what (the `Edge.created_by` is just `human`, not *which* human), or handling of reviewer disagreement.

---

## Smaller notes

- **`status` enum is overloaded** (§Data Model): `active | stale | archived | pending_review` mixes lifecycle (active/archived), freshness (stale), and workflow (pending_review). These are orthogonal axes and should be separate fields, or you'll hit illegal combinations (a node that is both `stale` and `pending_review`).
- **`confidence` is a single float on the node** but confidence is produced per-extraction-type by the model (§AI Extraction). A node distilled from multiple signals needs a provenance-aware confidence, not one number.
- **Model choice is reasonable** (§Tech Stack): `claude-sonnet-4-6` is a valid current model ID and a sensible default for structured extraction. Worth noting structured outputs (`output_config.format`) should be used to guarantee parseable extraction rather than free-text parsing of the schema-guided prompt — the doc's prompt (§AI Extraction) implies free-text output, which is fragile.
- **`related_to` weighting** (§Edges) needs a stated similarity floor; without one, every node links to every other above noise and the graph becomes a hairball that makes GAG traversal meaningless.

---

## Bottom line

The tiered model and the AI-proposes/human-confirms loop are good bones. The design is weakest exactly where its differentiation lives: the incremental-indexing failure modes (§3) and conflict resolution (§1.1) are sketched, not engineered, and Phase 1 (§4.1) defers them so far that the MVP won't validate the core claim. The two omissions that should block sign-off are **access control** (§5.1 — a correctness/safety issue for company knowledge) and a **conflict/dispute lifecycle in the data model and query layer** (§1.1, §2.3). Add `node_type`, versioning fields, and `tenant_id` to the schema now (§2) — they are cheap today and migrations later. Re-cut the phases around the incremental hypothesis.
