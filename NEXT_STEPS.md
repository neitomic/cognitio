# Cognitio — Next Implementation Steps

## Target

Build the smallest trustworthy end-to-end slice:

> one configured Notion page subtree → immutable source snapshot → normalized text and stable
> chunks → one validated, evidence-backed extraction → one embedding → ACL-filtered search result
> with source provenance

Then add manual review and incremental re-indexing without changing the storage or job contracts.
This is narrower than all of Phase 1, but it validates the two highest-risk claims early:

1. Notion block trees can be rendered into stable text whose character offsets survive extraction.
2. A model can reliably return `extraction.v1` records with evidence spans that verify against that
   text.

Rough effort assumes one experienced engineer working full-time. Estimates include focused tests and
review, but not production UI, deployment hardening, or Phase 2/3 work.

## What exists now

The repository has useful structural contracts but almost no durable implementation:

- The seven packages, dependency direction, Pydantic extraction schema, span verifier, fingerprint
  function, review/search service shells, FastAPI routes, job payloads, and worker loop are present.
- `cognitio_extraction.schema`, `validator`, `fingerprint`, `prompt`, and the basic
  `ClaudeExtractor` contain real logic that can be unit-tested now.
- `cognitio_query.SearchService`, `AclResolver`, `ReviewService`, `PromotionPolicy`, retry policy,
  and API routes contain partial domain behavior.
- Storage is not bootstrapped. `cognitio_storage.__init__` imports missing `models.py` and `db.py`;
  there are no repositories, migrations, or Alembic configuration.
- Every pipeline handler and every Notion connector operation is a stub. The worker composition root
  is also a stub.
- There are no tests, fixtures, CI workflow, Docker Compose file, environment template, or runnable
  application configuration.
- `uv.lock` is a placeholder that `uv` cannot parse, so even the documented test/install commands
  currently fail.

## Gaps to resolve before implementation

These are contract gaps, not just unfinished method bodies:

1. **Queue transaction ownership is inconsistent.** `Worker.run_once()` calls a handler with a
   transaction-like object, then calls `queue.complete()` separately. That cannot guarantee the
   documented invariant that handler writes, job completion, and follow-on enqueue commit together.
   Storage must own one `Uow` around all three operations.
2. **Connector checkpoints need a durable home.** `SyncCursorStore` needs one current row per
   `(tenant_id, connector, scope)`. Reconstructing current cursor, reconciliation health, and scan
   membership from `change_events` is ambiguous and expensive. Add an explicit
   `connector_sync_states` table and retain `change_events` as the idempotent event log.
3. **Tombstone reconciliation needs previous scan membership.** Notion has no deletion feed. Store
   which source IDs were observed in a completed scoped scan (for example
   `connector_scan_items`) so an absent item can be archived only after a successful full scan.
4. **The searchable embedding object is undefined.** `EmbedPayload` includes `chunk_id`, but the
   `embeddings` uniqueness key and table design do not. The search API returns extraction IDs, while
   the documented DAG embeds chunks before extractions exist. For the prototype, embed each
   extraction's canonical searchable text after extraction and key the row by
   `(tenant_id, object_type='extraction', object_id, model_version)`. Normalized-chunk embeddings can
   be added later with a first-class chunk table or a stable chunk object ID.
5. **Chunks are only JSON inside `normalized_documents`.** This makes changed-chunk lookup, text
   retrieval, and per-chunk job inputs awkward. Add a typed `normalized_chunks` table rather than
   repeatedly querying and mutating a JSON array. Keep document-global `start_char`/`end_char` and
   `chunk_hash`; use deterministic chunk IDs.
6. **Extraction offset semantics need a spike.** The model sees only `Chunk.text`, but evidence
   offsets must address the entire normalized document. Define model output as chunk-local offsets,
   translate them by `chunk.start_char`, then validate and persist document-global spans. Update the
   prompt and tests so this is explicit.
7. **Notion does not expose complete object ACLs to an integration.** For the prototype, define an
   honest fallback ACL (configured tenant-visible principals or integration-visible scope), record
   `permission_metadata=False`, and label this as unsuitable for production multi-user isolation.
   Do not claim exact Notion ACL parity until a source/identity strategy exists.
8. **ACL filtering is currently too late.** `SearchService` asks the repository for ANN candidates
   before resolving ACLs and filters only the returned shortlist in Python. Resolve the principal
   first and pass `ResolvedAcl` into the repository so SQL applies ACL predicates before the ANN
   limit/ranking boundary.
9. **API services have no composition root.** `create_app()` registers routes but never supplies
   `search_service`, `review_service`, `source_service`, `sync_service`, or
   `review_detail_service`.

## Ordered implementation plan

### 0. Make the repository runnable and establish test infrastructure

**Effort: 1–2 days**

Files:

- root `uv.lock`, `pyproject.toml`
- `.env.example`
- `compose.yaml`
- `.github/workflows/ci.yml`
- `tests/conftest.py` and package-local `tests/`

Work:

- Regenerate a valid lockfile with `uv lock`; verify `uv sync --frozen`.
- Add `compose.yaml` using `pgvector/pgvector:pg16`, with a health check and a disposable test
  database. Document `DATABASE_URL` and `TEST_DATABASE_URL` in `.env.example`.
- Add CI jobs for `ruff check`, `mypy`, unit tests, and Postgres integration tests.
- Configure pytest markers (`unit`, `integration`, `live`) so normal CI never requires Notion,
  Anthropic, or embedding API credentials.
- Add basic import tests for every package. This immediately catches the currently missing Storage
  modules and package dependency mistakes.
- Add fixture factories for tenant IDs, source snapshots, normalized documents/chunks, extraction
  envelopes, and ACLs.

Exit criteria:

- `uv sync --frozen`, `uv run ruff check .`, `uv run mypy packages apps`, and `uv run pytest` execute
  successfully.
- CI starts Postgres with `vector` and `pgcrypto` available.

Why first: no later estimate is credible until contributors and CI can run the same environment.

### 1. Lock down the two high-risk pure transformations with a thin spike

**Effort: 2–3 days**

Files:

- `packages/connectors/src/cognitio_connectors/notion/render.py` (new)
- `packages/pipeline/src/cognitio_pipeline/normalization.py` (new)
- `packages/pipeline/src/cognitio_pipeline/chunking.py` (new)
- `packages/extraction/src/cognitio_extraction/prompt.py`
- `packages/extraction/src/cognitio_extraction/client.py`
- `packages/extraction/src/cognitio_extraction/validator.py`
- corresponding unit fixtures/tests

Work:

- Implement a deterministic Notion block renderer for the initial supported set: paragraph,
  headings, bulleted/numbered list items, to-do, quote, code, callout, toggle, child page, and
  table/table-row. Preserve operative wording; include stable separators and block IDs in metadata,
  not in visible text.
- Define normalization rules explicitly: UTF-8 decode, newline normalization, Unicode normalization,
  and conservative whitespace handling. Do not collapse text in ways that change meaning.
- Implement deterministic chunking with document-global offsets, configurable maximum size and
  overlap, and `sha256(normalized_text[start_char:end_char])`.
- Use deterministic chunk IDs derived from source version plus boundary/hash, not random UUIDs.
- Change the extraction boundary so the model returns chunk-local spans; translate them to
  document-global offsets before `SpanVerifier.verify_envelope()`.
- Add golden tests proving that rendering and chunking the same block tree twice produces identical
  text, boundaries, IDs, hashes, and evidence offsets.
- Add one opt-in live Claude test over a fixed fixture. Measure schema-validation and span-verification
  success; save sanitized failures as test fixtures.

Exit criteria:

- A fixed Notion fixture deterministically produces stable text/chunks.
- A sample decision or fact extracted from a non-zero-offset chunk verifies against the full
  normalized document.

Why now: stable provenance is Cognitio's trust boundary. If this fails, storage and orchestration
work should not hide the problem.

### 2. Implement the minimal Storage layer and forward migration

**Effort: 4–6 days**

Files:

- `packages/storage/src/cognitio_storage/models.py` (new)
- `packages/storage/src/cognitio_storage/db.py` (new)
- `packages/storage/src/cognitio_storage/types.py` (new)
- `packages/storage/src/cognitio_storage/repositories/` (new package)
- `packages/storage/alembic.ini` (new)
- `packages/storage/src/cognitio_storage/migrations/env.py` (new)
- `packages/storage/src/cognitio_storage/migrations/versions/0001_initial.py` (new)

Tables required for the vertical slice:

- `source_items`
- `source_versions`
- `normalized_documents`
- `normalized_chunks` (fill the skeleton gap described above)
- `extractions`
- `entity_mentions`
- `embeddings`
- `jobs`
- `change_events`
- `connector_sync_states`
- `connector_scan_items`
- `review_items`
- `cost_events`
- `principals`

Defer `entities`, `entity_merges`, `edges`, and `conflicts` until the narrow slice works, unless
creating the full initial migration is cheaper than staging a second migration. If included, leave
their services unwired.

Required constraints/indexes:

- `tenant_id NOT NULL` everywhere and tenant-scoped unique keys.
- Partial unique indexes for current source versions/documents/extractions.
- Unique source item `(tenant_id, connector, source_id)`.
- Immutable source version `(tenant_id, source_item_id, content_hash)`.
- Extraction fingerprint uniqueness for current rows.
- Evidence-spans non-empty check and Gold/gold-source consistency check.
- Job dedupe uniqueness that permits multiple rows when `dedupe_key IS NULL`; do not let SQL `NULL`
  semantics accidentally weaken intended idempotency.
- Embedding uniqueness by object and model version, plus an HNSW partial index for the active
  dimension/version.
- Foreign keys that are tenant-safe where practical. Avoid a cross-tenant UUID reference merely
  because IDs are globally unique in normal operation.

Repositories/functions needed immediately:

- `SourceItemRepository.upsert_ref()`, `get_for_update()`, `advance_revision()`,
  `set_current_version()`, `archive_missing()`
- `SourceVersionRepository.insert_if_new()`, `get()`, `get_current()`
- `NormalizedDocumentRepository.insert()`, `get()`
- `NormalizedChunkRepository.replace_for_document()`, `list_for_document()`,
  `get_by_chunk_id()`, `diff_against_previous_version()`
- `ExtractionRepository.insert_if_absent()`, `by_chunk()`, `mark_stale()`,
  `mark_rederived()`, `searchable_text()`
- `EmbeddingRepository.upsert()`, `semantic_candidates()`
- `CostEventRepository.insert()`
- `ChangeEventRepository.insert_if_absent()`, `mark_done()`, `mark_failed()`
- `ConnectorSyncStateRepository.load()`, `checkpoint()`, `record_health()`
- `JobRepository.enqueue()`, `claim()`, `complete_with_follow_ons()`, `fail()`,
  `requeue_stuck()`

Implement `Uow` with an async SQLAlchemy session and repository properties. Add migration tests that
upgrade an empty database to head and exercise every constraint above.

Exit criteria:

- An integration test inserts one complete source → version → document → chunk → extraction →
  embedding chain and reads it back tenant-scoped.
- Concurrent revision and fingerprint tests prove regressions/duplicates are no-ops.
- Migration upgrade succeeds on a clean Postgres instance.

Why here: every connector, pipeline, review, and query operation depends on these transactional
primitives.

### 3. Fix and implement the job queue transaction contract

**Effort: 2–3 days**

Files:

- `packages/pipeline/src/cognitio_pipeline/queue.py`
- `packages/pipeline/src/cognitio_pipeline/worker.py`
- `packages/storage/src/cognitio_storage/repositories/jobs.py`
- `packages/storage/src/cognitio_storage/db.py`

Work:

- Replace the marker `Transaction` with a typed transaction/UoW protocol exposing the repositories
  handlers use.
- Make one operation own the atomic boundary. Recommended shape:
  `JobRunner.run_claimed(job)` opens `async with uow`, invokes the handler, writes its domain rows,
  marks the job done, and enqueues follow-ons before commit.
- Keep `claim()` short-lived and atomic using `FOR UPDATE SKIP LOCKED`; do not hold a database row
  lock across Notion/model network calls. Claim by changing status, then execute externally, then
  finalize in a new transaction with an ownership/status guard.
- Implement exponential retry, `run_after`, dead-letter transition, and stale-processing-job reaper.
- Validate persisted payloads through the discriminated `JobPayload` union when enqueueing and
  loading.
- Add concurrency tests with two workers, crash-after-handler/finalization tests, dedupe tests, and
  retry/dead-letter tests.

Exit criteria:

- No job is executed by two healthy workers.
- A crash cannot leave a completed domain write without either completing the job and enqueueing its
  children or safely retrying idempotently.

Why before handlers: the queue's correctness determines whether every later stage can be retried.

### 4. Implement a fixture connector and the Notion fetch/reconciliation path

**Effort: fixture connector 1 day; Notion connector 4–6 days**

Files:

- `packages/connectors/src/cognitio_connectors/fixture.py` (new)
- `packages/connectors/src/cognitio_connectors/notion/client.py`
- `packages/connectors/src/cognitio_connectors/notion/connector.py`
- `packages/connectors/src/cognitio_connectors/notion/render.py`
- `packages/connectors/src/cognitio_connectors/sync_state.py`
- `packages/pipeline/src/cognitio_pipeline/jobs/reconcile.py` (new)
- `packages/pipeline/src/cognitio_pipeline/jobs/fetch.py`
- `packages/pipeline/src/cognitio_pipeline/types.py`

Work:

- Add an in-memory/fixture connector implementing the full `Connector` protocol. Use it for
  deterministic end-to-end CI before involving external APIs.
- Implement an `httpx` Notion API adapter with auth, API version header, pagination, timeout,
  rate-limit handling, and bounded retries.
- Implement scoped `full_scan()` from configured roots, recursive `fetch_children()`, complete
  block-tree `fetch()`, canonical raw JSON serialization, content hash, source timestamp, and a
  monotonic per-item revision.
- Add a `RECONCILE` job type. It loads `connector_sync_states`, scans pages, idempotently records
  `change_events`, upserts `source_items`, and enqueues `FETCH` jobs. Checkpoint only after each page
  of scan events is durably recorded.
- On a successful completed full scan, compare `connector_scan_items` with the previous generation
  and emit tombstones/archive missing source items. Never infer deletion from a failed/partial scan.
- Store explicit health timestamps and last error so token expiry is distinguishable from no
  changes.
- For the Notion ACL limitation, inject a configured fallback `AccessDescriptor` and preserve it in
  `source_versions.acl_snapshot`.

Exit criteria:

- Fixture reconciliation creates source/change/fetch jobs idempotently.
- A recorded Notion fixture with nested/paginated blocks produces one canonical snapshot.
- Repeating an unchanged scan does not create a new source version.
- An out-of-order fetch cannot replace a newer version.

Why: connector synchronization is the second major product risk and unlocks the real ingestion path.

### 5. Implement normalize, chunk, and invalidation handlers

**Effort: 3–4 days**

Files:

- `packages/pipeline/src/cognitio_pipeline/jobs/normalize.py`
- `packages/pipeline/src/cognitio_pipeline/jobs/chunk.py`
- `packages/pipeline/src/cognitio_pipeline/jobs/invalidate.py`
- transformation modules from Step 1

Work:

- `NormalizeHandler.run()` loads immutable raw content, renders/normalizes it, creates one
  `normalized_documents` row, and enqueues `CHUNK`.
- `ChunkHandler.run()` computes/stores deterministic `normalized_chunks`, compares them with the
  previous current source version, and enqueues:
  - `EXTRACT` for new/changed chunks;
  - `INVALIDATE` for prior chunks that disappeared or changed.
- Do not enqueue `EMBED` yet; extraction embeddings are created after validated extraction.
- `InvalidateHandler.run()` marks only current extractions derived from affected prior chunks stale.
  Re-extraction is driven by the new chunk's normal `EXTRACT` job; removed chunks archive or
  supersede prior records according to an explicit policy.
- Keep current-row swaps and child-job enqueue in the finalization transaction.

Exit criteria:

- Changing one paragraph in a multi-chunk fixture schedules extraction only for affected chunks.
- Unchanged chunks retain their extractions; removed chunks cannot appear as current search results.
- Re-running any handler is a no-op beyond already-deduplicated jobs/rows.

Why: this proves the incremental mechanism before adding model and embedding cost.

### 6. Complete extraction persistence and review-item creation

**Effort: 4–5 days**

Files:

- `packages/extraction/src/cognitio_extraction/client.py`
- `packages/extraction/src/cognitio_extraction/prompt.py`
- `packages/extraction/src/cognitio_extraction/mapping.py` (new)
- `packages/pipeline/src/cognitio_pipeline/jobs/extract.py`
- relevant Storage repositories

Work:

- Implement the Anthropic `StructuredClaudeClient` adapter using structured outputs and the
  `ExtractionEnvelope.model_json_schema()`.
- Add one bounded repair/retry for schema or span failures. Preserve sanitized failure metadata on
  the job/dead letter; never write a partially valid envelope.
- Map decisions/actions/facts/open questions to individual `extractions` with:
  `node_type`, typed payload, promoted fields, translated global evidence spans, per-record
  fingerprint, confidence, inherited ACL, `trust_state=extracted`,
  `workflow=pending_review`, and current/version fields.
- Store entity outputs as `entity_mentions`; defer canonical resolution. Skip relationship/edge
  persistence in the first slice unless both referenced records were persisted successfully.
- Insert exactly one `cost_events` row per model call in the same successful extraction transaction.
- Create one open `review_items` row per persisted extraction. Phase 1 must never auto-promote.
- Enqueue one `EMBED` job per new extraction with a dedupe key containing extraction ID and model
  version.

Tests:

- schema rejection, local-ID validation, span translation/verification, fingerprint idempotency,
  ACL inheritance, promoted fields, one-cost-row guarantee, and no partial writes.

Exit criteria:

- A fixture or live model response atomically creates validated extractions, mentions, review items,
  cost event, and embedding follow-ons.
- Re-running extraction creates no duplicate current extraction or review item.

Why: this is the central trust boundary and completes connector → storage → extraction.

### 7. Implement extraction embeddings and ACL-safe semantic query

**Effort: 3–4 days**

Files:

- `packages/pipeline/src/cognitio_pipeline/jobs/embed.py`
- `packages/query/src/cognitio_query/search.py`
- `packages/query/src/cognitio_query/acl.py`
- `packages/query/src/cognitio_query/repositories.py` (new)
- `packages/api/src/cognitio_api/routes/search.py`

Work:

- Add an embedding provider protocol shared by indexing and query; implement the configured
  `text-embedding-3-small` adapter and a deterministic fake for tests.
- Define canonical searchable text by node type (for example decision title + decision, fact claim,
  action description). `EmbedHandler` loads this text and upserts an extraction embedding pinned to
  a full model/version identifier.
- Resolve principal/source identities before candidate lookup. Change
  `SemanticSearchRepository.candidates()` to accept `ResolvedAcl`, and apply tenant, lifecycle,
  `is_current`, model version, similarity floor, and effective-ACL predicates in SQL before `LIMIT`.
- Make search **hybrid lexical + vector**, not pure-pgvector. Add a `tsvector`/GIN full-text index
  over the searchable content of `normalized_documents` and `extractions` (decision title/text,
  fact claim, action description) — add the FTS columns/indexes to the migration. Run an ANN leg and
  an FTS leg, both with the **same ACL/tenant/lifecycle/`is_current` predicates applied in SQL before
  `LIMIT`**, then fuse the two result sets with **Reciprocal Rank Fusion (RRF)** (or weighted-score
  fusion) into the final ranking. Exact-term queries (names, IDs, error strings) must be reachable
  via the lexical leg.
- Join extraction → source version → source item to build `SearchCandidate`/`SourceSummary`; return
  evidence/provenance via the source drilldown, not hidden ad-hoc SQL in the API.
- Keep disputed/stale weighting already present in `SearchService`, but exclude archived and
  superseded records.
- Add two-tenant and denied-principal integration tests proving an invisible high-similarity record
  cannot displace visible results or leak through source endpoints.

Exit criteria:

- Querying a phrase from the ingested page returns its extraction with similarity, tier,
  freshness, source URL, and no cross-tenant/ACL leakage.

Why: this completes the requested narrow end-to-end prototype.

### 8. Wire runnable worker and API composition roots; add an end-to-end test

**Effort: 2–3 days**

Files:

- `apps/worker/src/cognitio_worker/main.py`
- `packages/api/src/cognitio_api/main.py`
- `packages/api/src/cognitio_api/composition.py` (new)
- concrete source/sync/detail services under `packages/api/src/cognitio_api/`
- `tests/e2e/test_fixture_vertical_slice.py`

Work:

- Load typed settings for database, Notion, Anthropic, embedding model/version, worker polling, and
  connector scope.
- Register connector instances and all implemented handlers in the worker.
- Supply concrete `sync_service`, `search_service`, `source_service`, and review-detail services on
  FastAPI lifespan startup. Keep route modules transport-only.
- Add readiness checks for DB connectivity/extensions and configured provider clients; retain
  `/healthz` as a process liveness check.
- End-to-end CI test: fixture connector reconciliation → run jobs until idle → assert source,
  normalized text/chunks, extraction, review item, embedding → call `/search` with tenant/principal
  headers → verify provenance.

Exit criteria:

- `uv run cognitio-worker` and the API start from documented environment variables.
- The fixture-backed end-to-end test passes without network access.
- A manual Notion smoke test can ingest one configured page and find one extracted record.

### 9. Add manual review to complete the trust loop

**Effort: 3–4 days**

Files:

- `packages/review/src/cognitio_review/queue.py`
- Storage review repository
- `packages/api/src/cognitio_api/routes/review.py`
- review/source detail services

Work:

- Implement cursor-paginated review listing and evidence-first detail retrieval.
- In one transaction:
  - confirm → `trust_state=gold`, `gold_source=human_review`, `workflow=none`;
  - edit → validate payload for the extraction node type, version/supersede the old extraction,
    create corrected Gold, preserve evidence, and record `before`/`after`;
  - reject → archive the extraction and record the negative decision.
- Require tenant and ACL checks on detail and mutation paths. Header auth remains a development-only
  boundary.
- Test concurrent decisions, repeated requests, invalid edits, and audit immutability.

Exit criteria:

- One extracted record can be confirmed into Gold and immediately ranks with the Gold weight.
- Every decision has an immutable audit row.

Why after search: review is easier to validate when the trust-state change has visible query
behavior, while not blocking the first end-to-end slice.

### 10. Harden incremental operation and production diagnostics

**Effort: 4–6 days**

Files:

- connector/pipeline maintenance jobs
- API sync/source status services
- logging/metrics configuration
- runbooks in `docs/`

Work:

- Add scheduled reconciliation, stuck-job reaping, dead-letter inspection/retry, connector health,
  queue-depth, stale-extraction backlog, validation-failure rate, and cost summaries.
- Test a real page edit: a changed chunk creates a new source version, stales only affected
  extractions, re-extracts, and prevents old/current duplicates in search.
- Implement deletion lifecycle for reconciliation tombstones. Keep right-to-deletion and
  crypto-shredding as a separate, explicitly tracked security milestone; do not imply ordinary
  archival satisfies it.
- Add query/promotion access audit rows or a dedicated audit table before inviting multiple users.
- Build a small golden extraction fixture set and report precision/recall and review override rate
  before changing prompts/models.

Exit criteria:

- The vertical slice survives retries, out-of-order fetches, one-page edits, no-op rescans, worker
  crashes, and tombstones.
- Operators can distinguish idle, backlogged, rate-limited, credential-failed, and dead-lettered
  states.

## Critical path and parallel work

Critical path:

```text
tooling → provenance spike → storage/UoW → queue contract → fixture connector
→ normalize/chunk → extraction persistence → embedding/search → composition/e2e
```

Expected time to the fixture-backed end-to-end prototype: **21–32 engineering days**.
Expected time to a Notion-backed prototype with manual Gold review and basic hardening:
**32–48 engineering days**.

After Step 2 defines stable repository contracts, work can split:

- Engineer A: queue, handlers, worker composition.
- Engineer B: Notion client/rendering/reconciliation.
- Engineer C: extraction adapter/mapping and embedding/query.

Storage schema and offset semantics should not be developed independently in parallel because both
define the durable provenance contract.

## Build and test in isolation

- **Pure unit tests:** Notion rendering, normalization, chunk boundaries/hashes, prompt construction,
  offset translation, span verification, fingerprints, retry pricing/policy, ACL set logic, search
  scoring, promotion policy.
- **Postgres integration tests:** migrations, partial unique indexes, monotonic revisions, job
  claiming/dedupe/retry, current-row swaps, extraction idempotency, ANN queries, tenant and ACL
  predicates, review transactions.
- **Contract tests with fakes:** Connector protocol, structured Claude client, embedding provider,
  worker handler DAG.
- **Recorded API fixtures:** Notion pagination/block trees/rate limits; sanitized Claude structured
  responses. Keep live tests opt-in.
- **End-to-end test:** use the fixture connector and fake model/embedder in CI; reserve a separate
  smoke test for real Notion and provider credentials.

## Explicitly defer

Do not put these on the prototype critical path:

- canonical entity resolution beyond storing mentions;
- `supports`/`contradicts`, conflict detection, and dispute resolution;
- auto-promotion;
- graph traversal/GAG and synthesized records;
- second connector and cross-source identity resolution;
- materialized `related_to` edges;
- React review UI (exercise review through API first);
- graph database, external queue, or external vector database.

They remain compatible with the proposed schema, but none is required to prove one trustworthy
incremental source-backed extraction can be found and reviewed.

## Phase 2 / Phase 3 follow-on notes

These are not on the prototype critical path, but they extend deferred work and should be scheduled
with the phase that lights up each subsystem.

- **Phase 2 — blocking index for entity resolution and contradiction candidates.** When the entity
  resolution pass and the contradiction detector are built, include building the **two-stage blocking
  index** so neither does an O(n²) all-pairs scan: a `pg_trgm` GIN index on normalized entity
  names/aliases (and on normalized claim text) for the lexical block, plus pgvector ANN over
  entity/fact embeddings filtered by shared subject entity for the semantic block. Only the union of
  both blocks reaches the expensive resolution model / contradiction classifier.
- **Phase 2 — calibrate edge thresholds.** The materialization thresholds and fan-out caps for
  `supports`/`contradicts` (`supports` ≥ 0.7, ≤ 50/Gold fact; `contradicts` ≥ 0.8, ≤ 20) are
  starting points. Calibrate them against the **contradiction-detector eval set** before relying on
  the materialized edges, balancing false positives (queue flood) against false negatives (silent
  Gold corruption).
- **Phase 3 — head-to-head GAG vs. flat-RAG eval.** As a Phase 3 milestone alongside GAG, add an A/B
  eval: graph-augmented generation vs. flat semantic RAG on the **same question set**, graded by a
  **blind LLM-as-judge**, tracking answer-quality, token-usage, and tool-call-count deltas. This is
  the test that proves the graph earns its added complexity.
