# Cognitio — Implementation Task Breakdown

## Target

Build a trustworthy vertical slice:

> one configured Notion page subtree → immutable source snapshot → normalized text and stable
> chunks → validated evidence-backed extractions → review into Gold → ACL-filtered hybrid search
> with source provenance

Then prove incremental re-indexing, conflict-safe graph growth, and whether graph-augmented
generation beats flat RAG.

## Final review findings

- **Order:** Infrastructure must be runnable before estimates are meaningful, and Storage contracts
  must land before connector, queue, review, or query implementations. Pure rendering/extraction
  fixtures can be developed early, but durable handlers depend on the schema and `Uow`.
- **Missing prerequisites in the old plan:** valid lockfile, settings and environment contract,
  Docker Compose, test fixtures, CI, migration smoke tests, fixture connector, API/worker
  composition roots, access audit, readiness checks, and operational maintenance jobs.
- **Oversized steps:** the old Storage, Notion, extraction, search, and hardening steps each mixed
  several independently risky changes. They were not credible 2–6 day solo tasks. The tasks below
  are scoped to 2–6 focused hours each; allow **6–9 calendar weeks** for one experienced developer
  after integration/debugging contingency, provider/API surprises, and review.
- **Four codebase-memory-mcp improvements:** hybrid lexical/vector retrieval is on the Phase 1
  critical path; MinHash/LSH blocking is explicit rather than being approximated only by trigram
  search; persisted `supports`/`contradicts` edges enforce confidence floors and per-node caps; and
  GAG must pass a blind head-to-head evaluation against flat RAG.
- **Stub gaps:** Storage imports nonexistent `models.py` and `db.py`; all pipeline handlers and the
  worker composition root are stubs; `EmbedPayload` incorrectly requires a chunk for extraction
  embeddings; no `RECONCILE` job exists; extraction prompts describe document-global offsets even
  though the model sees chunk-local text; search filters ACLs after ANN limiting; API routes have no
  concrete services; and `entity_resolve.py`, `conflicts.py`, and `graph.py` do not yet encode the
  blocking, edge-cap, or evaluation contracts required by the design.
- **Remaining low-severity review gaps, folded into existing tasks (no renumbering):** the
  crypto-shred columns (`enc_key_id` + encrypted `raw_content`) ARCHITECTURE requires from day 1 are
  now explicit in task 7; the cheap "don't even fetch an unchanged page" pre-fetch gate (review gap
  E) is explicit in task 17; the deletion fail-safe taxonomy (review gap F) is already covered by
  task 18's "failed partial scans archive nothing"; and explicit non-silent query failures (review
  gap G) are added to task 36's acceptance.

Each numbered item below is intended to fit one focused 2–6 hour coding session. Dependencies refer
to task numbers, not phase names.

## Phase 0: Infra

1. **Repair the uv workspace and lockfile** *(2–4h)*
   - **Implement:** Regenerate root `uv.lock`; correct dependency/version conflicts in root and
     member `pyproject.toml` files; add missing test/runtime dependencies only where used.
   - **Acceptance:** `uv sync --frozen` succeeds from a clean checkout and every package imports.
   - **Dependencies:** None.

2. **Define typed application settings** *(3–5h)*
   - **Implement:** Add a small settings module used by `packages/api` and `apps/worker`, covering
     `DATABASE_URL`, `TEST_DATABASE_URL`, Notion token/root IDs, Anthropic key/model, embedding
     provider/model version, fallback ACL principals, and worker timing; add `.env.example`.
   - **Acceptance:** Unit tests validate required fields, defaults, secret redaction, and invalid
     URLs; API and worker can construct settings without reading environment variables directly.
   - **Dependencies:** 1.

3. **Add local Postgres infrastructure** *(2–4h)*
   - **Implement:** Add `compose.yaml` using `pgvector/pgvector:pg16`, health checks, persistent dev
     storage, and a disposable test database; document start/stop/reset commands in `README.md`.
   - **Acceptance:** `docker compose up -d` reaches healthy state and both databases expose
     `vector` and `pgcrypto`.
   - **Dependencies:** 1.

4. **Create the test harness and fixture factories** *(4–6h)*
   - **Implement:** Configure pytest markers `unit`, `integration`, and `live`; add root
     `tests/conftest.py` with database lifecycle helpers and factories for tenants, ACLs, source
     snapshots, normalized documents/chunks, extraction envelopes, and jobs.
   - **Acceptance:** Unit tests run without Docker or provider credentials; integration tests skip
     with an actionable message when `TEST_DATABASE_URL` is absent.
   - **Dependencies:** 1, 3.

5. **Add dependency-boundary checks and continuous integration** *(4–6h)*
   - **Implement:** Add smoke tests importing all packages, a check that rejects upward layer
     imports according to `AGENTS.md`, and `.github/workflows/ci.yml` with frozen install,
     `ruff check`, `mypy`, unit tests, and pgvector-backed integration tests; exclude `live` tests.
   - **Acceptance:** Missing modules and invalid upward imports fail locally and in CI; CI runs the
     same commands documented for local development and fails on lint, type, migration, or tests.
   - **Dependencies:** 1, 3, 4.

## Phase 1: Storage + Connectors

6. **Create SQLAlchemy base types and database lifecycle** *(4–6h)*
   - **Implement:** Add `packages/storage/src/cognitio_storage/models.py`, `types.py`, and `db.py`
     with naming conventions, UUID/timestamp helpers, async engine/session factories, and an async
     `Uow` commit/rollback context.
   - **Acceptance:** `cognitio_storage` imports successfully; unit tests prove `Uow` commits on
     success and rolls back on error.
   - **Dependencies:** 1, 4.

7. **Model source, sync, and normalization tables** *(4–6h)*
   - **Implement:** In `models.py`, define `source_items`, `source_versions`,
     `normalized_documents`, typed `normalized_chunks`, `change_events`,
     `connector_sync_states`, and `connector_scan_items`, including tenant-safe foreign keys and
     current-row constraints; add the crypto-shred columns now (`source_versions.enc_key_id` plus
     `raw_content` typed as the per-record encrypted unit) so right-to-deletion needs no later
     schema retrofit, per ARCHITECTURE's day-1 rule.
   - **Acceptance:** Metadata tests assert all required columns, unique keys, revision fields,
     chunk offsets/hashes, scan generations, checkpoint health, the `enc_key_id`/encrypted
     `raw_content` crypto-shred unit, and tenant predicates exist.
   - **Dependencies:** 6.

8. **Model extraction, review, and embedding tables** *(4–6h)*
   - **Implement:** Define `extractions`, `entity_mentions`, `embeddings`, `review_items`,
     `principals`, and `cost_events`; add non-empty evidence checks, Gold/gold-source consistency,
     promoted query columns, searchable text/`tsvector`, and model-version uniqueness.
   - **Acceptance:** Metadata tests cover extraction fingerprint/current uniqueness, review audit
     fields, one embedding per object/version, and all required indexes.
   - **Dependencies:** 6.

9. **Model queue, entity, conflict, edge, and audit tables** *(4–6h)*
    - **Implement:** Define `jobs`, `entities`, `entity_merges`, `edges`, `conflicts`,
      `access_audit_events`, and `blocking_signatures`; preserve no-FK polymorphic edges and include
      fields needed for MinHash signatures, edge provenance, thresholds, and evaluation sampling.
    - **Acceptance:** Metadata tests cover nullable job dedupe semantics, claim index, conflict
      status index, edge lookup indexes, and immutable audit payloads.
   - **Dependencies:** 6.

10. **Create and verify the initial Alembic migration** *(4–6h)*
    - **Implement:** Add Alembic configuration/environment and `0001_initial.py`; create
      `pgcrypto`/`vector`, enums, tables, partial unique indexes, GIN FTS/trigram indexes, and the
      active-version HNSW index explicitly.
    - **Acceptance:** A clean database upgrades to head; schema inspection matches tasks 7–9; a
      dev-only downgrade and re-upgrade succeeds.
   - **Dependencies:** 7, 8, 9.

11. **Implement source and sync repositories** *(4–6h)*
    - **Implement:** Add repositories for source items/versions, change events, sync states, and
      scan membership with `upsert_ref`, monotonic `advance_revision`, `insert_if_new`,
      checkpoint/health updates, generation completion, and archive-missing operations.
    - **Acceptance:** Integration tests prove duplicate events/snapshots are no-ops, revisions
      cannot regress, checkpoints advance on empty pages, and incomplete scans never archive data.
   - **Dependencies:** 10.

12. **Implement document, chunk, and extraction repositories** *(4–6h)*
    - **Implement:** Add normalized document/chunk insert/get/diff methods and extraction
      insert/version/stale/archive/searchable-text methods; expose them through `Uow`.
    - **Acceptance:** An integration test writes and reads a tenant-scoped
      source→version→document→chunk→extraction chain; duplicate fingerprints and cross-tenant reads
      are rejected.
   - **Dependencies:** 10.

13. **Implement queue, review, embedding, edge, and audit repositories** *(4–6h)*
    - **Implement:** Add typed repositories for jobs, review items, embeddings, entities/conflicts,
      capped edges, blocking signatures, costs, principals, and access audits.
    - **Acceptance:** Repository integration tests cover job dedupe, open-review lookup, embedding
      upsert, immutable audit insertion, and edge writes that can be transactionally guarded.
   - **Dependencies:** 10.

14. **Build a deterministic fixture connector** *(3–5h)*
    - **Implement:** Add `packages/connectors/src/cognitio_connectors/fixture.py` implementing the
      full `Connector` protocol, including pagination, edits, permission changes, tombstones,
      failures, and stable content hashes.
    - **Acceptance:** Contract tests exercise full/incremental scans, child expansion, fetch,
      retries, tombstones, and deterministic replay without network access.
    - **Dependencies:** 4.

15. **Implement the Notion HTTP adapter and recorded fixtures** *(4–6h)*
    - **Implement:** Replace the `NotionApi` protocol-only boundary with an `httpx` adapter handling
      auth/version headers, pagination, timeouts, `429 Retry-After`, bounded retries, and typed error
      mapping; add sanitized recorded responses.
    - **Acceptance:** Tests cover multi-page search/children, timeout, 429, 5xx, and malformed
      responses without live credentials.
    - **Dependencies:** 2, 4.

16. **Implement deterministic Notion rendering** *(4–6h)*
    - **Implement:** Add `notion/render.py` for paragraph, headings, lists, to-do, quote, code,
      callout, toggle, child page, table, and table-row blocks; preserve block IDs as metadata and
      use stable visible separators.
    - **Acceptance:** Golden fixtures render identically across runs and preserve exact text needed
      for evidence offsets.
   - **Dependencies:** 14, 15.

17. **Implement scoped Notion scanning and fetching** *(4–6h)*
    - **Implement:** Complete `NotionConnector.full_scan`, `incremental_scan`, `fetch_children`, and
      `fetch`; scope traversal to configured roots, serialize canonical raw JSON, derive monotonic
      revisions, and apply the configured fallback ACL with `permission_metadata=False`. Add the
      cheap pre-fetch gate: skip fetching a page's block tree entirely when its `last_edited_time`
      (plus child-count/size hints) is unchanged versus the recorded sync state, before any hashing.
    - **Acceptance:** Recorded nested/paginated fixtures produce stable refs and snapshots;
      unchanged fetches keep the same hash; pages with an unchanged `last_edited_time` are skipped
      without a block-tree fetch; out-of-scope pages are excluded.
   - **Dependencies:** 15, 16.

18. **Implement reconciliation and tombstone detection** *(4–6h)*
    - **Implement:** Add connector reconciliation service logic using tasks 11 and 17: persist each
      page of events before checkpointing, track scan generations, update health, and emit
      tombstones only after a successful completed full scan.
    - **Acceptance:** Repeated scans are idempotent; empty scans advance health/checkpoints; failed
      partial scans archive nothing; a missing prior member is archived after a complete scan.
   - **Dependencies:** 11, 14, 17.

## Phase 2: Pipeline + Extraction

19. **Define payloads and implement SKIP LOCKED queue operations** *(4–6h)*
    - **Implement:** Add `RECONCILE` and maintenance payloads, fix extraction embedding payloads,
      and update the Pipeline README DAG; back `JobQueue.enqueue`, `claim`, `fail`, and
      `requeue_stuck` with short `FOR UPDATE SKIP LOCKED` transactions, ownership, exponential
      backoff, dead-letter state, and JSON validation through the `JobPayload` union.
    - **Acceptance:** Payloads round-trip through JSON; two workers never receive one healthy job;
      retry timing, max-attempt, stale-lock, and dedupe tests pass.
    - **Dependencies:** 13, 18.

20. **Make handler finalization atomic** *(4–6h)*
    - **Implement:** Replace the marker `Transaction` with a typed handler `Uow` protocol; add a
      runner that performs idempotent domain writes, ownership-guarded completion, and follow-on
      enqueue in one final transaction without holding locks during network calls.
    - **Acceptance:** Crash-point tests show a retry cannot produce an unpaired domain write, job
      completion, or child enqueue.
    - **Dependencies:** 6, 19.

21. **Add reconcile and fetch handlers** *(4–6h)*
    - **Implement:** Add `jobs/reconcile.py`; complete `FetchHandler` using the connector registry,
      monotonic source revision guard, immutable source-version insert, ACL snapshot, and deduped
      `NORMALIZE` follow-on.
    - **Acceptance:** Fixture reconciliation creates fetch work; unchanged and out-of-order
      snapshots create no new current version or duplicate child job.
    - **Dependencies:** 18, 19, 20.

22. **Implement normalization rules and handler** *(3–5h)*
    - **Implement:** Add `pipeline/normalization.py` with UTF-8/newline/Unicode and conservative
      whitespace rules; complete `NormalizeHandler` to render connector content, persist one
      document, and enqueue `CHUNK`.
    - **Acceptance:** Golden fixtures normalize deterministically without changing operative
      wording; rerunning the job is a no-op.
    - **Dependencies:** 12, 16, 20, 21.

23. **Implement stable chunking and chunk diffs** *(4–6h)*
    - **Implement:** Add `pipeline/chunking.py` with configurable size/overlap, document-global
      offsets, deterministic IDs, and SHA-256 text hashes; complete `ChunkHandler` to persist chunks
      and enqueue only changed/new extraction plus prior-chunk invalidation.
    - **Acceptance:** Repeated chunking is byte-stable; a one-paragraph edit schedules only affected
      chunks; removed chunks are reported explicitly.
    - **Dependencies:** 12, 20, 22.

24. **Implement per-record invalidation and supersession** *(3–5h)*
    - **Implement:** Complete `InvalidateHandler` and extraction repository transitions for changed
      and removed chunks; mark affected current extractions stale, archive removed derivations, and
      preserve unaffected records.
    - **Acceptance:** Integration tests show unchanged extractions remain current, stale flags clear
      only after replacement commits, and removed-chunk records disappear from current search.
    - **Dependencies:** 12, 20, 23.

25. **Make extraction offsets explicitly chunk-local** *(3–5h)*
    - **Implement:** Update `extraction/prompt.py`, `client.py`, and `validator.py` so model output
      offsets address `Chunk.text`, then translate every record/relationship span by
      `chunk.start_char` before validating against the full normalized document.
    - **Acceptance:** Unit tests verify valid spans from a non-zero-offset chunk and reject
      out-of-range, mismatched, or already-global model spans.
    - **Dependencies:** 4, 23.

26. **Implement the Anthropic structured-output adapter** *(4–6h)*
    - **Implement:** Add a concrete `StructuredClaudeClient` using the Anthropic SDK, schema-bound
      output, timeouts, request IDs, token usage, and one bounded repair/retry for schema/span
      failures; sanitize stored failure details.
    - **Acceptance:** Recorded/fake tests cover success, malformed schema, span failure, repair
      success, terminal failure, and exact token accounting; one opt-in `live` test is documented.
    - **Dependencies:** 2, 25.

27. **Map extraction envelopes to durable records** *(4–6h)*
    - **Implement:** Add `extraction/mapping.py` to map decisions, actions, facts, open questions,
      and entities to typed extraction/mention writes with promoted fields, global evidence,
      fingerprints, inherited ACL, and canonical searchable text.
    - **Acceptance:** Unit tests cover every node type, deterministic fingerprints, ACL inheritance,
      invalid local references, and promoted field values.
    - **Dependencies:** 8, 12, 25.

28. **Complete the extraction handler transaction** *(4–6h)*
    - **Implement:** Complete `ExtractHandler`: load document/chunk/context, call the extractor,
      persist all-or-nothing extractions/mentions, exactly one cost row per model call, one open
      review item per extraction, and deduped extraction `EMBED` jobs; do not persist relationships
      until their endpoints exist.
    - **Acceptance:** A retry creates no duplicate extraction, review item, cost event, or embed job;
      any invalid record causes no partial durable output.
    - **Dependencies:** 13, 20, 26, 27.

29. **Implement embedding providers and handler** *(4–6h)*
    - **Implement:** Add a shared embedding protocol, deterministic fake, and configured OpenAI
      adapter; complete `EmbedHandler` to embed canonical extraction text and upsert by extraction
      ID plus full model/version identifier.
    - **Acceptance:** Fake-provider tests are deterministic; retries do not duplicate vectors;
      model versions never share an ANN index/query space.
    - **Dependencies:** 2, 13, 19, 20, 28.

30. **Implement MinHash/LSH candidate blocking** *(4–6h)*
    - **Implement:** Add `query/blocking.py` (or a lower-layer utility with no upward dependency) to
      normalize claim/entity n-grams, compute deterministic MinHash signatures, assign LSH bands,
      persist `blocking_signatures`, and union LSH candidates with trigram/ANN candidates filtered
      by tenant and shared subject entity.
    - **Acceptance:** Tests show near-duplicates enter the candidate set, unrelated records are
      substantially pruned, signatures are reproducible, and candidate generation performs no
      all-pairs scan.
    - **Dependencies:** 9, 13, 27, 29.

## Phase 3: Review + Query

31. **Implement evidence-first review and provenance reads** *(4–6h)*
    - **Implement:** Complete review list/get operations with stable cursor pagination and filters;
      back `ReviewDetailService` and `SourceService` through Storage repositories with ACL-safe
      extraction/source/version joins, evidence text, source URL, confidence, tier, freshness,
      workflow, and provenance.
    - **Acceptance:** Queue, detail, search-source, and drilldown tests return consistent exact
      evidence only for visible tenant records; inaccessible objects return not-found without
      revealing existence.
    - **Dependencies:** 12, 13, 28.

32. **Implement atomic review decisions** *(4–6h)*
    - **Implement:** Implement confirm/edit/reject in one `Uow`: confirm promotes to human-reviewed
      Gold; edit schema-validates, versions/supersedes the old extraction, and creates corrected
      Gold; reject archives it; always preserve immutable before/after audit.
    - **Acceptance:** Concurrent/repeated decisions are idempotent or conflict explicitly; invalid
      edits roll back; every successful decision has reviewer/time/before/after.
    - **Dependencies:** 12, 13, 27, 31.

33. **Implement ACL-safe hybrid retrieval and RRF** *(4–6h)*
    - **Implement:** Resolve `ResolvedAcl` before retrieval; add extraction/document FTS and
      pgvector ANN repository queries that require identical tenant, allow/deny, lifecycle,
      freshness/current, trust-state, and model-version predicates before `LIMIT`; fuse ranked IDs
      with Reciprocal Rank Fusion and retain tier/freshness/dispute weighting.
    - **Acceptance:** Exact names, IDs, and error strings are found lexically; semantic paraphrases
      are found by ANN; an invisible higher-scoring record cannot displace a visible result;
      deterministic RRF, two-tenant, and deny-precedence tests pass.
    - **Dependencies:** 8, 10, 13, 29.

34. **Implement contradiction detection and capped edge writes** *(4–6h)*
    - **Implement:** Use task 30 candidates in a separately scored contradiction classifier;
      persist `supports` only at confidence ≥0.7 with ≤50 outgoing edges per Gold fact and
      `contradicts` only at ≥0.8 with ≤20; replace/drop the weakest edge transactionally; create
      conflict sets and sample borderline cases for evaluation.
    - **Acceptance:** Below-threshold edges are absent; caps survive concurrent writers; strongest
      edges remain; high-confidence contradictions open one idempotent dispute set.
    - **Dependencies:** 13, 26, 30, 32.

35. **Implement bounded graph context assembly and edge GC** *(4–6h)*
    - **Implement:** Complete `query/graph.py` with ACL-filtered seed search, typed traversal,
      per-edge fan-out/node/depth/character budgets, depth-one computed `related_to`, provenance,
      and dispute warnings; add an orphan-edge integrity/GC maintenance operation.
    - **Acceptance:** Traversal never exceeds budgets, never crosses ACL/tenant boundaries, warns
      on disputes, computes rather than stores `related_to`, and GC removes dangling edges.
    - **Dependencies:** 31, 33, 34.

## Phase 4: API + Eval

36. **Wire worker and API composition roots** *(4–6h)*
    - **Implement:** Add `cognitio_api/composition.py`; construct engine/Uow, repositories,
      connectors, providers, handlers, search/review/source/sync/detail services, and worker
      registry from typed settings; add graceful shutdown.
    - **Acceptance:** `uv run cognitio-worker` and `uv run uvicorn cognitio_api.main:app` start from
      `.env.example`-documented settings and no route returns “service not configured”; malformed or
      unsupported query/search requests return an explicit error rather than a silently empty result
      that is indistinguishable from “no matches.”
    - **Dependencies:** 2, 20, 21, 22, 23, 24, 28, 29, 31, 32, 33.

37. **Add operational APIs, audit, and runbooks** *(4–6h)*
    - **Implement:** Keep `/healthz` as liveness; add readiness for DB/extensions/provider config;
      back connector health/reconcile routes, queue/dead-letter/stale-backlog/cost summaries, and
      query/promotion access-audit writes; document setup, migration, reconciliation, dead-letter
      replay, credential failure, model-version rollover, index rebuild, rollback, and the opt-in
      real-provider smoke test.
    - **Acceptance:** Operators can distinguish idle, backlogged, rate-limited, credential-failed,
      and dead-lettered states; every search and review mutation emits a tenant/principal audit row;
      a new developer can follow the runbook from a clean checkout.
    - **Dependencies:** 5, 10, 13, 18, 19, 32, 33, 36.

38. **Add fixture-backed end-to-end and incremental tests** *(4–6h)*
    - **Implement:** Add `tests/e2e/test_fixture_vertical_slice.py`: reconcile fixture connector,
      drain jobs, assert snapshot/document/chunks/extractions/review/embeddings, call review/search/
      drilldown APIs, edit one paragraph, rerun, and verify selective invalidation.
    - **Acceptance:** The complete test passes without network access and proves no-op rescans,
      retries, current-row uniqueness, ACL isolation, Gold promotion, and changed-chunk-only work.
    - **Dependencies:** 36, 37.

39. **Build extraction and contradiction evaluation harnesses** *(4–6h)*
    - **Implement:** Add versioned golden datasets and an `eval/` runner reporting extraction
      precision/recall by node type, span-verification rate, review override rate, contradiction
      precision/recall, prompt/model/version metadata, and regression thresholds.
    - **Acceptance:** The runner produces machine-readable and human-readable reports and exits
      nonzero when configured precision floors regress.
    - **Dependencies:** 26, 27, 32, 34.

40. **Run blind GAG vs flat-RAG head-to-head evaluation** *(4–6h)*
    - **Implement:** Add a fixed question/corpus set and two answer paths using the same model:
      flat hybrid RAG from task 33 and graph context from task 35; randomize labels for a blind
      LLM-as-judge and record answer quality, token use, latency, and tool-call count.
    - **Acceptance:** Repeated runs emit paired per-question results and aggregate confidence
      intervals; the report states whether GAG improves quality enough to justify its added cost.
    - **Dependencies:** 35, 39.

## Completion boundary

Tasks 1–38 deliver the trustworthy Phase-1 product slice with manual Gold review and hybrid search.
Tasks 30 and 34 incorporate LSH blocking and edge-hairball controls needed before conflict features
are trusted. Tasks 35 and 40 are the gate for retaining graph complexity: if GAG does not beat flat
RAG at acceptable cost, keep hybrid retrieval and defer further graph expansion.
