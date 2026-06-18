# Cognitio — Architecture

This document decomposes Cognitio into **seven independently implementable layers**. It is the companion to [`DESIGN.md`](./DESIGN.md): DESIGN explains *what* the system is and *why*; ARCHITECTURE explains *how* to build it, layer by layer.

Each layer below specifies:

- **Responsibilities** — what the layer owns.
- **Key interfaces / types** — the public surface it exposes upward, as Python type hints / pseudocode.
- **Dependencies** — what it needs from the layers below.
- **Implementation notes / gotchas** — the non-obvious traps.

## Layering principle

```
┌─────────────────────────────────────────────────────────────┐
│ 7. API Layer            FastAPI — sync, review, search, cost │
├─────────────────────────────────────────────────────────────┤
│ 6. Query Layer          semantic search · GAG · ACL @ query  │
├─────────────────────────────────────────────────────────────┤
│ 5. Review Layer         review lifecycle · promotion · audit │
├─────────────────────────────────────────────────────────────┤
│ 4. Extraction Layer     Claude · extraction.v1 · evidence    │
├─────────────────────────────────────────────────────────────┤
│ 3. Pipeline Layer       job queue (SKIP LOCKED) · workers    │
├─────────────────────────────────────────────────────────────┤
│ 2. Connector Layer      capability-aware sync · Notion       │
├─────────────────────────────────────────────────────────────┤
│ 1. Storage Layer        Postgres + pgvector · typed tables   │
└─────────────────────────────────────────────────────────────┘
```

Dependencies point **downward only**. A higher layer may call the public interface of any layer below it; no layer calls upward. Two cross-cutting concerns — **ACL propagation** and **cost accounting** — are not their own layers; they are invariants threaded through Storage (columns), Extraction (cost rows), and Query (enforcement). They are called out explicitly in each affected layer.

Every interface in this document carries an implicit `tenant_id`. It is omitted from signatures for brevity but is a mandatory predicate on every query and a non-null column on every row.

---

## Layer 1 — Storage Layer

The foundation. Owns the Postgres schema, migrations, and typed access to every table. Every other layer reaches durable state **only** through this layer's repository functions — no ad-hoc SQL above Layer 1.

### Responsibilities

- Define the Postgres schema: tables, columns, types, constraints, indexes.
- Own migrations (forward-only, versioned) and the `pgvector` / `pgcrypto` extensions.
- Provide typed repository functions (CRUD + the handful of domain queries) per table.
- Enforce structural invariants the DB *can* enforce (NOT NULL, CHECK, unique, partial-unique for `is_current`).
- Provide the transactional primitive (`async with uow() as tx:`) used by the Pipeline Layer for atomic multi-row commits.
- Host the typed-JSON validators (Pydantic) for the few `jsonb` columns that carry schema-validated payloads.

### Schema

All tables carry `id uuid PK default gen_random_uuid()`, `tenant_id uuid NOT NULL`, `created_at timestamptz NOT NULL default now()`. Enums are Postgres `ENUM` types (listed once, reused).

```sql
-- Enum types ---------------------------------------------------------------
CREATE TYPE lifecycle_t   AS ENUM ('active','archived');
CREATE TYPE freshness_t   AS ENUM ('current','stale');
CREATE TYPE workflow_t    AS ENUM ('none','pending_review','disputed');
CREATE TYPE trust_state_t AS ENUM ('extracted','gold','superseded');
CREATE TYPE gold_source_t AS ENUM ('human_review','authoritative_source','auto_promoted');
CREATE TYPE node_type_t   AS ENUM ('decision','action','fact','entity_ref','open_question');
CREATE TYPE entity_type_t AS ENUM ('person','team','product','system','customer',
                                   'vendor','project','repository','document','metric','other');
CREATE TYPE edge_type_t   AS ENUM ('derived_from','references','supersedes','supports','contradicts');
CREATE TYPE provenance_t  AS ENUM ('human','model','vector','parser');
CREATE TYPE change_type_t AS ENUM ('created','updated','deleted','permission_changed');
CREATE TYPE job_status_t  AS ENUM ('pending','processing','done','failed','dead_letter');

-- 1. source_items : logical external object (a page, a thread) ---------------
CREATE TABLE source_items (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid NOT NULL,
  node_type          text,                          -- connector object kind ('page','database',...)
  connector          text NOT NULL,                 -- 'notion'
  source_id          text NOT NULL,                 -- stable external id
  source_url         text,
  current_version_id uuid,                           -- FK -> source_versions (nullable; set after first fetch)
  source_revision    bigint NOT NULL DEFAULT 0,      -- monotonic; writes can't regress it
  acl                jsonb NOT NULL DEFAULT '{}',    -- captured access descriptor (principals/groups)
  lifecycle          lifecycle_t NOT NULL DEFAULT 'active',
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, connector, source_id)
);

-- 2. source_versions : Tier 0 immutable raw snapshots -----------------------
CREATE TABLE source_versions (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL,
  source_item_id   uuid NOT NULL REFERENCES source_items(id),
  content_hash     bytea NOT NULL,                  -- sha256 of raw bytes
  raw_content      bytea NOT NULL,                  -- encrypted-at-rest (crypto-shred unit)
  enc_key_id       uuid,                            -- per-record key ref for crypto-shredding
  fetched_metadata jsonb NOT NULL DEFAULT '{}',
  acl_snapshot     jsonb NOT NULL DEFAULT '{}',     -- ACL as captured at fetch time
  source_timestamp timestamptz,                     -- source's own last-edited time
  fetched_at       timestamptz NOT NULL DEFAULT now(),
  is_current       boolean NOT NULL DEFAULT true,
  UNIQUE (tenant_id, source_item_id, content_hash)
);
CREATE UNIQUE INDEX one_current_version ON source_versions (source_item_id)
  WHERE is_current;

-- 3. normalized_documents : Tier 1 normalized text + stable chunks ----------
CREATE TABLE normalized_documents (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL,
  source_version_id uuid NOT NULL REFERENCES source_versions(id),
  normalized_text   text NOT NULL,                  -- offsets are stable against THIS
  language          text,
  chunks            jsonb NOT NULL,                 -- [{chunk_id,start_char,end_char,chunk_hash}]
  is_current        boolean NOT NULL DEFAULT true,
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_current_norm ON normalized_documents (source_version_id)
  WHERE is_current;

-- 4. extractions : Tier 2 / Tier 3 typed extracted records ------------------
CREATE TABLE extractions (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            uuid NOT NULL,
  node_type            node_type_t NOT NULL,
  source_version_id    uuid NOT NULL REFERENCES source_versions(id),
  normalized_document_id uuid NOT NULL REFERENCES normalized_documents(id),
  chunk_id             text NOT NULL,
  payload              jsonb NOT NULL,              -- schema-validated typed JSON (extraction.v1)
  -- generated columns promoted from payload for cheap structured queries:
  owner_entity_id      uuid,
  due_date             date,
  item_status          text,
  claim_type           text,
  evidence_spans       jsonb NOT NULL,             -- [{start_char,end_char,text}]  REQUIRED
  fingerprint          bytea NOT NULL,            -- hash(type+normalized_claim+span+source_version_id)
  confidence           real,
  effective_acl        jsonb NOT NULL DEFAULT '{}', -- union of source denies
  trust_state          trust_state_t NOT NULL DEFAULT 'extracted',
  gold_source          gold_source_t,
  lifecycle            lifecycle_t NOT NULL DEFAULT 'active',
  freshness            freshness_t NOT NULL DEFAULT 'current',
  workflow             workflow_t  NOT NULL DEFAULT 'none',
  version              int NOT NULL DEFAULT 1,
  is_current           boolean NOT NULL DEFAULT true,
  created_at           timestamptz NOT NULL DEFAULT now(),
  CHECK (jsonb_array_length(evidence_spans) >= 1),       -- evidence is mandatory
  CHECK (trust_state <> 'gold' OR gold_source IS NOT NULL)
);
CREATE UNIQUE INDEX uniq_extraction_fp ON extractions (tenant_id, fingerprint)
  WHERE is_current;
CREATE INDEX ix_extr_owner   ON extractions (tenant_id, owner_entity_id) WHERE is_current;
CREATE INDEX ix_extr_due     ON extractions (tenant_id, due_date)        WHERE is_current;
CREATE INDEX ix_extr_trust   ON extractions (tenant_id, trust_state)     WHERE is_current;
CREATE INDEX ix_extr_flow    ON extractions (tenant_id, workflow)        WHERE workflow <> 'none';
CREATE INDEX ix_extr_stale   ON extractions (tenant_id) WHERE freshness = 'stale';
CREATE INDEX ix_extr_payload ON extractions USING gin (payload);

-- 5. entity_mentions : mention spans in source text -------------------------
CREATE TABLE entity_mentions (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            uuid NOT NULL,
  extraction_id        uuid REFERENCES extractions(id),
  normalized_document_id uuid NOT NULL REFERENCES normalized_documents(id),
  surface_form         text NOT NULL,
  span                 jsonb NOT NULL,            -- {start_char,end_char,text}
  resolved_entity_id   uuid REFERENCES entities(id),  -- null until resolution pass
  confidence           real,
  created_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_mention_unresolved ON entity_mentions (tenant_id)
  WHERE resolved_entity_id IS NULL;
CREATE INDEX ix_mention_entity ON entity_mentions (tenant_id, resolved_entity_id);

-- 6. entities : Tier 3 canonical, post-resolution ---------------------------
CREATE TABLE entities (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL,
  node_type      entity_type_t NOT NULL,
  canonical_name text NOT NULL,
  aliases        jsonb NOT NULL DEFAULT '[]',     -- public-ish identity (not ACL-restricted)
  attributes     jsonb NOT NULL DEFAULT '[]',     -- [{value,source_version_id,effective_acl}]
  lifecycle      lifecycle_t NOT NULL DEFAULT 'active',
  version        int NOT NULL DEFAULT 1,
  is_current     boolean NOT NULL DEFAULT true,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_entity_name ON entities (tenant_id, lower(canonical_name)) WHERE is_current;

CREATE TABLE entity_merges (                        -- audit + reversibility for merge/split
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            uuid NOT NULL,
  operation            text NOT NULL,             -- 'merge' | 'split'
  surviving_entity_id  uuid,
  merged_entity_ids    jsonb NOT NULL,
  reassigned_mention_ids jsonb NOT NULL,
  performed_by         uuid,
  performed_at         timestamptz NOT NULL DEFAULT now()
);

-- 7. edges : typed relationships (NO FKs - app-enforced integrity) ----------
CREATE TABLE edges (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL,
  from_id       uuid NOT NULL,
  from_type     text NOT NULL,
  to_id         uuid NOT NULL,
  to_type       text NOT NULL,
  type          edge_type_t NOT NULL,
  confidence    real,                              -- edges are inferred -> own confidence
  provenance    provenance_t NOT NULL,
  reviewer_id   uuid,                              -- which human, if human-created
  evidence_spans jsonb,
  valid_from    timestamptz,                       -- temporal validity of the underlying fact
  valid_to      timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_edge_from ON edges (tenant_id, from_id, type);
CREATE INDEX ix_edge_to   ON edges (tenant_id, to_id, type);

-- 8. conflicts : first-class resolution unit --------------------------------
CREATE TABLE conflicts (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            uuid NOT NULL,
  member_ids           jsonb NOT NULL,            -- records in the conflict set
  contradicts_edge_ids jsonb NOT NULL,
  detector_confidence  real,
  proposed_resolution  jsonb,
  status               text NOT NULL DEFAULT 'open',  -- 'open' | 'resolved'
  resolved_by          uuid,
  resolved_at          timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_conflict_open ON conflicts (tenant_id) WHERE status = 'open';

-- 9. review_items : workflow + audit trail ----------------------------------
CREATE TABLE review_items (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL,
  target_id       uuid NOT NULL,
  target_type     text NOT NULL,
  workflow        workflow_t NOT NULL,
  reviewer_id     uuid,
  decision        text,                            -- 'confirm' | 'edit' | 'reject'
  before          jsonb,                           -- captured for eval / override-rate
  after           jsonb,
  cost_attributed numeric(12,6),
  created_at      timestamptz NOT NULL DEFAULT now(),
  decided_at      timestamptz
);
CREATE INDEX ix_review_open ON review_items (tenant_id, created_at)
  WHERE decided_at IS NULL;

-- 10. embeddings : separate table, version-aware ----------------------------
CREATE TABLE embeddings (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL,
  object_type   text NOT NULL,                     -- what is embedded
  object_id     uuid NOT NULL,
  model         text NOT NULL,
  model_version text NOT NULL,                      -- queries pin to one version
  vector        vector(1536) NOT NULL,             -- pgvector; dim per model
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, object_type, object_id, model_version)
);
-- HNSW index is per model_version. Build one per active version; query pins version.
CREATE INDEX ix_emb_hnsw_v1 ON embeddings USING hnsw (vector vector_cosine_ops)
  WHERE model_version = 'v1';

-- 11. change_events : per-source, idempotent (sync state) -------------------
CREATE TABLE change_events (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL,
  connector       text NOT NULL,
  source_id       text NOT NULL,
  cursor          text,
  high_watermark  text,
  change_type     change_type_t NOT NULL,
  source_revision bigint,
  status          job_status_t NOT NULL DEFAULT 'pending',
  attempts        int NOT NULL DEFAULT 0,
  next_retry_at   timestamptz,
  processed_at    timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, connector, source_id, source_revision)
);

-- 12. principals : Cognitio identity <-> per-source identities --------------
CREATE TABLE principals (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id                uuid NOT NULL,
  cognitio_user_id         uuid NOT NULL,
  source_identities        jsonb NOT NULL DEFAULT '[]',  -- [{connector,source_user_id}]
  group_memberships_cache  jsonb,
  cache_refreshed_at       timestamptz,
  UNIQUE (tenant_id, cognitio_user_id)
);

-- 13. jobs : pipeline queue (see Pipeline Layer) ----------------------------
CREATE TABLE jobs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL,
  type          text NOT NULL,                     -- fetch|normalize|chunk|embed|extract|entity_resolve|invalidate
  payload       jsonb NOT NULL,
  status        job_status_t NOT NULL DEFAULT 'pending',
  priority      int NOT NULL DEFAULT 100,
  attempts      int NOT NULL DEFAULT 0,
  max_attempts  int NOT NULL DEFAULT 5,
  run_after     timestamptz NOT NULL DEFAULT now(),
  locked_at     timestamptz,
  locked_by     text,
  last_error    text,
  dedupe_key    text,                              -- idempotency for enqueue
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, type, dedupe_key)
);
CREATE INDEX ix_jobs_claimable ON jobs (priority, run_after)
  WHERE status = 'pending';

-- 14. cost_events : per-tenant/source/job cost accounting -------------------
CREATE TABLE cost_events (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL,
  job_id        uuid,
  source_item_id uuid,
  kind          text NOT NULL,                     -- 'extraction' | 'embedding' | 'conflict' | ...
  model         text,
  input_tokens  bigint,
  output_tokens bigint,
  usd           numeric(12,6) NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_cost_tenant_day ON cost_events (tenant_id, created_at);
```

### Key interfaces / types

```python
# Repositories expose typed access; nothing above Layer 1 writes raw SQL.
class SourceItemRepo:
    async def upsert(self, item: SourceItem) -> SourceItem: ...
    async def get(self, item_id: UUID) -> SourceItem | None: ...
    async def bump_revision(self, item_id: UUID, new_rev: int) -> bool:
        """Monotonic guard: returns False (no-op) if new_rev <= source_revision."""

class ExtractionRepo:
    async def insert_if_absent(self, e: Extraction) -> InsertResult:
        """Idempotent on (tenant_id, fingerprint) where is_current."""
    async def mark_stale(self, ids: list[UUID]) -> None: ...
    async def set_trust(self, id: UUID, state: TrustState, source: GoldSource) -> None: ...

class Uow:                                  # transactional unit of work
    async def __aenter__(self) -> "Tx": ...
    async def __aexit__(self, *exc) -> None: ...   # commit on clean exit, rollback on error
```

### Dependencies

None. This is the bottom layer. Depends only on Postgres ≥ 16, `pgvector`, `pgcrypto`.

### Implementation notes / gotchas

- **`is_current` uniqueness is a partial unique index, not app logic.** `WHERE is_current` partial-unique indexes make "exactly one current row" a database guarantee. "Current-only" reads never traverse `supersedes`.
- **`edges` has no FKs by design** — it spans every node type. Referential integrity is an *application* invariant maintained by the orphan-GC job (Pipeline Layer). Do not add FKs here.
- **Generated columns vs. triggers.** `owner_entity_id`, `due_date`, etc. can be Postgres `GENERATED ... STORED` from `payload`, or written explicitly by the Extraction Layer at insert. Prefer explicit writes — generated-from-jsonb expressions are brittle across schema versions.
- **HNSW is one space per index.** Never mix `model_version` in a single HNSW index; cosine distances across model versions are meaningless. The partial index per version is the mechanism for blue/green re-embeds.
- **Crypto-shredding lives in the column design.** `raw_content` is encrypted with a per-record key (`enc_key_id`); right-to-deletion destroys the key, not the row. Build this in from day 1 — retrofitting encryption onto an append-only store is painful.
- **Migrations are forward-only and reviewed.** Use a single tool (Alembic or sqlc-style raw-SQL migrations). Never edit a shipped migration.

---

## Layer 2 — Connector Layer

Turns messy external SaaS APIs into a uniform, capability-aware sync contract. Produces `source_versions` (Tier 0) and `change_events`; produces nothing higher. The first and only Phase-1 implementation is **Notion**.

### Responsibilities

- Define the abstract `Connector` contract and the `ConnectorCapabilities` declaration.
- Implement the Notion connector: block-tree traversal, `last_edited_time` deltas, block-children fetch, permission capture, tombstone approximation.
- Own **sync state**: cursors, high-watermark checkpoints, retry/backoff, dead-letter — persisted in `change_events`.
- Capture the source ACL descriptor at fetch time into `source_versions.acl_snapshot`.
- Compute content hashes to skip no-op fetches.
- Run periodic reconciliation (the source of truth; webhooks are only a latency optimization).

### Key interfaces / types

```python
@dataclass(frozen=True)
class ConnectorCapabilities:
    incremental_cursor: bool        # supports a resumable change cursor
    updated_since_filter: bool      # can filter by updated-since
    webhooks: bool
    tombstones: bool                # can discover deletions
    permission_metadata: bool       # exposes per-object ACL
    child_expansion: bool           # has a parent/child tree to walk
    stable_content_hash: bool       # bytes are stable enough to hash for no-op skip

@dataclass(frozen=True)
class Page(Generic[T]):
    items: list[T]
    next_cursor: str | None
    high_watermark: str | None      # resumable position even when items is empty
    sync_started_at: datetime
    has_more: bool
    retry_after: float | None       # seconds; set on rate-limit

class Connector(Protocol):
    def capabilities(self) -> ConnectorCapabilities: ...
    async def full_scan(self, cursor: str | None) -> Page[SourceRef]: ...
    async def incremental_scan(self, cursor: str | None) -> Page[ChangeEvent]: ...
    async def fetch(self, ref: SourceRef) -> SourceSnapshot: ...
    async def fetch_children(self, ref: SourceRef, cursor: str | None) -> Page[SourceRef]: ...
    async def tombstone_scan(self, cursor: str | None) -> Page[Tombstone]: ...

@dataclass(frozen=True)
class SourceSnapshot:
    source_id: str
    raw_content: bytes
    content_hash: bytes             # sha256(raw_content)
    acl: AccessDescriptor           # principals/groups captured now
    source_timestamp: datetime | None
    source_revision: int            # monotonic where the source provides one
    metadata: dict
```

### Sync-state management

```python
class SyncCursorStore:                          # backed by change_events
    async def load(self, connector: str) -> str | None: ...
    async def checkpoint(self, connector: str, cursor: str, hwm: str) -> None: ...

class RetryPolicy:
    base: float = 2.0; max_attempts: int = 5
    def next_delay(self, attempts: int) -> float:   # exp backoff + jitter
        ...
    def is_dead_letter(self, attempts: int) -> bool: ...
```

A scan loop: `load cursor → scan → for each ChangeEvent upsert (idempotent on source_revision) → checkpoint(next_cursor, high_watermark)`. On failure, increment `attempts`, set `next_retry_at`; past `max_attempts` move to `dead_letter` and alert.

### Dependencies

- **Storage Layer** — `source_items`, `source_versions`, `change_events`, `principals` repositories.
- Does **not** depend on Pipeline; the Pipeline Layer *drives* the connector (calls `fetch`), not vice versa.

### Implementation notes / gotchas

- **Webhooks are at-least-once and lossy.** Treat them as a latency hint only. Periodic `incremental_scan` / reconciliation is the source of truth — a connector that only trusts webhooks silently drifts.
- **A wedged connector must be distinguishable from "no changes."** Token expiry / outage must surface in connector health, not look like an idle quiet period. Track `last_successful_reconciliation` and cursor lag.
- **`high_watermark` advances even when `items` is empty.** Otherwise a long quiet scan never checkpoints and re-scans from scratch after a crash.
- **Monotonic `source_revision` is enforced at write time** (`SourceItemRepo.bump_revision` no-ops on regression). Notion's `last_edited_time` is second-rounded and non-monotonic across objects — derive a per-item revision (page `last_edited_time` + a fetch sequence) rather than trusting a global timestamp.
- **Permission changes are content-invisible changes.** A permission delta must still create a `change_event` and re-fetch the ACL snapshot, even when `content_hash` is unchanged.
- **Notion deletions** aren't a first-class feed. Approximate via reconciliation: refs present last full scan and absent now → `Tombstone`. Scope full scans by database/page subtree for large workspaces.

---

## Layer 3 — Pipeline Layer

The orchestration engine. A Postgres-backed job queue with `SKIP LOCKED` and a pool of async workers that execute typed job stages. This layer turns "a source changed" into a deterministic, idempotent, resumable cascade across normalize → chunk → embed → extract → entity-resolve, and handles invalidation when a chunk changes.

### Responsibilities

- Own the `jobs` table, enqueue/claim/complete/fail semantics, retry/backoff, dead-letter.
- Define each job type's input/output contract and the DAG edges between them.
- Run the worker process: claim with `FOR UPDATE SKIP LOCKED`, execute a handler, commit results + follow-on enqueues in one transaction.
- Drive the Connector Layer (`fetch`) and call down into Extraction for `extract` jobs.
- Implement **invalidation propagation**: per-record `freshness = stale`, resumable, idempotent.
- Run periodic maintenance jobs: edge-integrity / orphan-GC, reconciliation triggers.

### Job queue

```sql
-- Claim one job atomically; invisible to other workers while locked.
UPDATE jobs SET status='processing', locked_at=now(), locked_by=$worker
WHERE id = (
  SELECT id FROM jobs
  WHERE status='pending' AND run_after <= now()
  ORDER BY priority, run_after
  FOR UPDATE SKIP LOCKED
  LIMIT 1
) RETURNING *;
```

### Key interfaces / types

```python
class JobQueue:
    async def enqueue(self, type: str, payload: dict, *,
                      dedupe_key: str | None = None,
                      priority: int = 100,
                      run_after: datetime | None = None) -> UUID:
        """Idempotent on (tenant, type, dedupe_key)."""
    async def claim(self, worker_id: str) -> Job | None:    # SKIP LOCKED
    async def complete(self, job: Job, *, enqueue: list[NewJob]) -> None:
        """Atomic: mark done + enqueue follow-ons in one tx."""
    async def fail(self, job: Job, err: str) -> None:       # backoff or dead_letter

class JobHandler(Protocol):
    type: str
    async def run(self, job: Job, tx: Tx) -> list[NewJob]:  # returns follow-on jobs
```

### Job types — input/output contracts

| Type | Input payload | Effect | Enqueues |
|------|---------------|--------|----------|
| `fetch` | `{source_item_id}` | calls `Connector.fetch`; writes `source_version` if `content_hash` is new; bumps revision (monotonic) | `normalize` (if new version) |
| `normalize` | `{source_version_id}` | clean text → `normalized_documents.normalized_text` + language | `chunk` |
| `chunk` | `{normalized_document_id}` | compute stable chunk boundaries + per-chunk hash; diff against prior current doc | `embed`, `extract` (per **changed** chunk only) |
| `embed` | `{object_type, object_id, chunk_id}` | embed text → `embeddings` (pinned `model_version`) | — |
| `extract` | `{normalized_document_id, chunk_id}` | call Extraction Layer; write validated `extractions` + `entity_mentions` + cost row | `entity_resolve` |
| `entity_resolve` | `{mention_ids}` or batch | cluster mentions → `entities`; set `resolved_entity_id` | — |
| `invalidate` | `{source_version_id, changed_chunk_ids}` | mark dependent extractions `freshness=stale` per-record | `extract` (re-derive each stale record) |

### Invalidation propagation

```python
async def invalidate(changed_chunk_ids: list[str], tx: Tx) -> list[NewJob]:
    affected = await extractions.by_chunk(changed_chunk_ids, current=True)
    await extractions.mark_stale([e.id for e in affected])      # per-record flag
    # one re-derive job per stale record; flag cleared only when THAT job commits
    return [NewJob('extract', {...e...}, dedupe_key=e.fingerprint) for e in affected]
```

### Dependencies

- **Storage Layer** — `jobs`, all content tables, `Uow`.
- **Connector Layer** — for `fetch` / reconciliation jobs.
- **Extraction Layer** — for `extract` jobs (called *down* into; Pipeline is the orchestrator).

### Implementation notes / gotchas

- **Complete + enqueue follow-ons in ONE transaction.** If a job marks itself done and enqueues children in separate commits, a crash between them loses the cascade. The `complete(job, enqueue=[...])` signature enforces atomicity.
- **Idempotency via `dedupe_key`.** Re-running `extract` for the same chunk must be a no-op (keyed on fingerprint downstream). The queue's unique `(tenant, type, dedupe_key)` prevents duplicate enqueues; the handlers must also be idempotent on their writes.
- **Per-record staleness, not all-or-nothing.** Each stale flag clears only when *that* record's re-derivation commits — so a crashed cascade resumes mid-flight. Do not clear a document-level flag.
- **Only changed chunks re-extract.** `chunk` diffs per-chunk hashes; a whole-document change that touches one paragraph enqueues one `extract`, not a full re-derive. This is the cost differentiator — get it right.
- **Visibility timeout / stuck jobs.** A worker that dies holding a lock leaves `status='processing'`. A reaper requeues jobs whose `locked_at` is older than a timeout.
- **Orphan-GC is correctness, not hygiene.** Dangling `contradicts`/`derived_from` edges produce wrong ACL and dispute answers. Run edge-integrity GC on a schedule.
- **Deletion does not re-derive.** A tombstone marks derived records `lifecycle=archived` and retains them; it never enqueues `extract`.

---

## Layer 4 — Extraction Layer

The intelligence boundary. Wraps the Claude API to turn one normalized chunk into validated, evidence-backed `extraction.v1` records. **Model output is untrusted until schema validation and evidence-span verification pass.** This layer's output is the input to Review.

### Responsibilities

- Build the extraction prompt (fixed schema/instruction prefix + per-chunk context header + chunk text).
- Call Claude with **structured outputs** — never free-text parsing.
- Validate every response against the `extraction.v1` JSON Schema / Pydantic models; route malformed JSON to a repair/retry path.
- Run the **offset-first evidence-span verifier** and reject records whose spans don't verify.
- Compute the deterministic fingerprint per record for idempotency.
- Map response-scoped `local_id`s to durable DB ids only after validation.
- Write a `cost_events` row per call (input/output tokens, model, USD).
- Apply model tiering: Haiku for entity/simple extraction, Sonnet for decisions/policies/conflict.

### Key interfaces / types

```python
class Extractor:
    async def extract(self, doc: NormalizedDoc, chunk: Chunk,
                      ctx: DocContextHeader) -> ExtractionResult:
        """End-to-end: prompt -> Claude (structured output) -> validate ->
           verify spans -> fingerprint. Never returns unverified records."""

@dataclass
class ExtractionResult:
    records: list[ValidatedExtraction]   # only schema-valid, span-verified survive
    mentions: list[EntityMention]
    relationships: list[ProposedEdge]
    warnings: list[Warning]
    cost: CostEvent                      # tokens + USD, written by this layer

class SpanVerifier:
    def verify(self, normalized_text: str, span: Span) -> VerifyOutcome:
        """Offset-first: start_char/end_char authoritative; `text` is a
           checksum compared after Unicode + whitespace normalization."""

def fingerprint(node_type: str, normalized_claim: str,
                span: Span, source_version_id: UUID) -> bytes:
    return sha256(f"{node_type}|{normalized_claim}|{span.start}:{span.end}|{source_version_id}")
```

The structured-output schema (`extraction.v1`) — entities, decisions, actions, facts, open_questions, relationships, warnings, each record carrying **required `evidence_spans`** with `{start_char, end_char, text}` — is defined in full in [`DESIGN.md` → AI Extraction Pipeline](./DESIGN.md). This layer's job is to *enforce* that schema, not redefine it.

### Cost accounting

```python
SONNET_IN, SONNET_OUT = 3.0/1e6, 15.0/1e6        # USD per token (DESIGN cost model)
def price(model: str, in_tok: int, out_tok: int) -> Decimal: ...
# Every extract() call writes exactly one cost_events row, attributed to job + source_item.
```

### Dependencies

- **Storage Layer** — writes `extractions`, `entity_mentions`, `cost_events`; reads `normalized_documents`.
- **Anthropic SDK** — Claude (`claude-haiku-4-5-20251001`, `claude-sonnet-4-6`) with structured outputs + prompt caching on the fixed prefix.
- Called by the Pipeline Layer's `extract` handler.

### Implementation notes / gotchas

- **Offsets are authoritative; `text` is a checksum.** Exact byte-matching the `text` field falsely rejects on whitespace/Unicode/paraphrase. Verify offsets in range against the immutable normalized text, then compare normalized `text` as a soft checksum. Reject only on out-of-range offsets or material divergence.
- **Offsets are against the normalized text, not the source.** The normalized text is immutable per version; the source is not. Always verify against `normalized_documents.normalized_text`.
- **`local_id`s are response-scoped.** Map to DB ids only after the whole response validates — a partially-written response corrupts referential intent.
- **Prompt-cache the fixed prefix.** The large schema/instruction prefix is identical on every call; cache it (≈ big cost saving). The per-chunk context header + text is the only variable part.
- **Confidence is per-record, not node-level, and is NOT trust.** The model is confidently wrong about owners/dates/decision-vs-proposal. Confidence feeds review prioritization, never auto-promotion on its own (see Review Layer).
- **Chunk-boundary context loss** is mitigated by overlapping windows + a parent-document context header passed to the extractor — pass it; don't extract a chunk in isolation.
- **Repair path is bounded.** Malformed JSON → one repair/retry → if still invalid, dead-letter the chunk with the raw response stored for eval. Don't loop forever burning tokens.

---

## Layer 5 — Review Layer

The human-in-the-loop trust boundary. Manages the lifecycle of an extracted record from `pending` to `confirmed`/`edited`/`rejected`, writes the audit trail, enforces promotion rules (what may become Gold and how), and owns the conflict-detection queue.

### Responsibilities

- Manage the review-item lifecycle and the three workflow states (`none` / `pending_review` / `disputed`).
- Apply promotion rules: human confirm → Gold; edit+confirm → corrected Gold (override captured); reject → negative example.
- Enforce the **auto-promotion gate** (Phase 2+): which records qualify for auto-promote vs. hard gate.
- Write every action to `review_items` (reviewer, timestamp, before/after, cost attribution).
- Own the conflict-detection queue and the dispute lifecycle (Phase 2): `conflicts` records, `disputed` workflow, resolution that clears the whole set atomically.

### Review-item lifecycle

```
extracted ──route──▶ pending_review ──confirm──▶ gold (human_review)
                          │ └────────edit+confirm─▶ gold (corrected, override logged)
                          │ └────────reject───────▶ archived (negative example)
                          └ hard_gate (blocks until human acts)

gold ──re-derived contradiction (Phase 2)──▶ disputed ──resolve set──▶ gold | superseded
```

### Promotion rules

```python
class PromotionPolicy:
    def route(self, e: Extraction) -> Disposition:
        """Phase 1: everything plausible -> pending_review; nothing auto-promotes."""

    def auto_promote_eligible(self, e: Extraction) -> bool:   # Phase 2+
        return (e.confidence >= 0.90
                and span_exactly_verified(e)
                and source_is_authoritative(e)
                and not has_unresolved_pronoun_or_relative_date(e)
                and not conflicts_with_gold(e)               # needs conflict detection
                and e.claim_type in LOW_RISK                 # state, simple metric only
                and passes_deterministic_validation(e))
        # decisions, policies, ownership, deadlines, commitments NEVER qualify.

class ReviewService:
    async def queue(self, filters: ReviewFilters) -> Page[ReviewItem]: ...
    async def decide(self, item_id: UUID, decision: Decision,
                     reviewer_id: UUID, edit: dict | None) -> None:
        """Writes review_items audit row + sets trust_state/workflow atomically."""
    async def resolve_conflict(self, conflict_id: UUID,
                               resolution: Resolution, reviewer_id: UUID) -> None:
        """Resolves the whole conflict set; clears `disputed` consistently."""
```

| Level | Trigger | Action |
|-------|---------|--------|
| **Auto-promote** (Phase 2+) | all gates pass, low-risk fact only | Extracted → Gold, full provenance logged |
| **Soft review** | plausible, not gate-passing | `workflow=pending_review`, shown in queue |
| **Hard gate** | low confidence / high-risk type / conflict | stays Extracted, blocks until a human acts |

### Conflict detection queue (Phase 2)

Contradiction detection is its **own** pipeline step with its **own** confidence — not a primitive. A dedicated classifier runs over candidate fact pairs (surfaced by semantic similarity + shared subject entities), emits a separate score, auto-opens a `conflict` on high confidence, samples borderline cases into the eval set. Detection runs against existing Gold by default; Extracted-vs-Extracted contradictions are surfaced opportunistically during review.

### Dependencies

- **Storage Layer** — `review_items`, `extractions` (trust/workflow), `conflicts`, `edges`.
- **Extraction Layer** — consumes its validated records; reuses its confidence + verified-span signals for gating.
- **Query Layer** (Phase 2 conflict detection) — uses semantic similarity to surface candidate pairs.

### Implementation notes / gotchas

- **Phase 1 has no auto-promotion at all.** Gold is reached only by human confirmation. Don't build the gate first — build trustworthy evidence-first review first.
- **Confidence ≥ 0.9 alone is never enough.** The gate is a conjunction of *all* conditions; high-risk types (decision/policy/ownership/deadline/commitment) are excluded regardless of confidence.
- **`disputed` is not demotion.** A disputed Gold record stays in the graph and is still returned at query time — with a warning and conflicting alternatives, never silently overwritten.
- **Resolve the whole conflict set atomically.** Multi-way / transitive conflicts are one `conflict` record; clearing `disputed` per-record piecemeal leaves the set inconsistent.
- **Edit captures an eval signal.** `before`/`after` on `review_items` is the override-rate quality metric — write both, always.

---

## Layer 6 — Query Layer

Read-side intelligence. Semantic search over `embeddings`, graph-augmented context assembly over `edges`, and **ACL enforcement before ranking and before any content reaches a prompt**. Phase 1 ships semantic search + ACL; GAG traversal is Phase 3.

### Responsibilities

- Semantic search: pgvector HNSW ANN, pinned to one embedding `model_version`, with a stated similarity floor.
- Resolve the requesting principal's effective permissions (live group resolution) and **filter candidates before ranking**.
- Graph-augmented generation (Phase 3): bounded, typed edge traversal; score-and-truncate; tier ranking; context budget.
- Surface provenance and dispute warnings in assembled context.

### Key interfaces / types

```python
class SearchService:
    async def search(self, principal: Principal, q: str, *,
                     model_version: str, sim_floor: float = 0.75,
                     limit: int = 20) -> list[SearchHit]:
        """1. embed q (same model_version as index)
           2. HNSW ANN over embeddings WHERE model_version pinned
           3. resolve principal ACL (live group membership)
           4. FILTER candidates by effective_acl   <-- before ranking
           5. rank by similarity x tier x freshness; return with provenance."""

@dataclass
class SearchHit:
    extraction_id: UUID
    tier: str                # gold > extracted > normalized
    similarity: float
    source: SourceRef
    confidence: float | None
    freshness: str
    workflow: str            # surfaces 'disputed' as a warning

class ContextAssembler:                          # Phase 3 (GAG)
    async def assemble(self, principal: Principal, seeds: list[UUID],
                       budget: ContextBudget) -> AssembledContext:
        """Bounded typed traversal:
           - walk curation edges (derived_from, supports, supersedes) to depth N
           - walk computed related_to only at depth 1
           - hard node-count / fan-out cap per edge type
           - score-and-truncate BEFORE assembly (never thousands of nodes)
           - rank by tier, recency, temporal validity
           - disputed records included with warning + alternatives."""

class AclResolver:
    async def effective_principals(self, p: Principal) -> ResolvedAcl:
        """Resolves group membership LIVE at query time (short-TTL cache in principals).
           Returns the deny/allow sets used to filter candidates."""
```

### ACL enforcement at query time

- A derived record is visible only to principals who could see **all** its sources — `effective_acl` is the **union of source denies**.
- Group membership is resolved **live** (short-TTL cache), never from a captured snapshot — avoids leaking to removed members and blocking newly-added ones.
- Filtering happens **before ranking** and **before any content reaches a prompt**.

### Dependencies

- **Storage Layer** — `embeddings` (HNSW), `extractions`, `edges`, `entities`, `principals`.
- **Extraction Layer** — indirectly, via the embeddings it produced (same `model_version` pin).
- **Connector / Pipeline** — for live group-membership resolution inputs (principal → source identities).

### Implementation notes / gotchas

- **Pin the embedding `model_version` on both write and query.** Mixing versions in one HNSW index makes distances meaningless. Re-embeds are blue/green; the query pins to one version at a time.
- **Stated similarity floor, always.** Without a floor, ANN returns a hairball of weak neighbors. `related_to` is computed at query time from `embeddings`, never materialized early.
- **Most-restrictive-wins over-restricts deliberately.** A fact also present in a public doc is hidden from public-only viewers. This is a safe default, not a bug — don't "fix" it without an explicit opt-in path (Phase 4).
- **Traversal is bounded and typed, never "depth N unbounded."** Per-edge-type fan-out caps + score-and-truncate before context assembly. A popular node otherwise explodes the context budget.
- **Disputed ≠ excluded.** Return disputed records with a warning and their conflicting alternatives; never assert one side as authoritative.

---

## Layer 7 — API Layer

The external surface. FastAPI routes for the review UI and operators: sync status, review-queue CRUD, search, source drilldown, cost dashboard. Thin — it validates requests, resolves the principal, and delegates to Layers 5/6; it contains no business logic of its own.

### Responsibilities

- Define HTTP routes, request/response models (Pydantic), and pagination.
- AuthN (resolve the caller to a `Principal`) and pass it down for AuthZ at the Query Layer.
- Map domain results to JSON; map domain errors to HTTP status codes.
- Stream/poll long-running sync + job status for the UI.

### Routes

```python
# Sync status -----------------------------------------------------------------
GET  /connectors                      -> [ConnectorHealth]   # sync state, cursor lag, dead-letter count
GET  /connectors/{c}/status           -> ConnectorHealth
POST /connectors/{c}/reconcile        -> {job_id}            # trigger full reconciliation

# Review queue CRUD -----------------------------------------------------------
GET  /review                          -> Page[ReviewItem]    # filter by workflow/node_type/source
GET  /review/{id}                     -> ReviewItemDetail    # record + evidence next to source
POST /review/{id}/confirm             -> ReviewItem          # -> gold (human_review)
POST /review/{id}/edit                -> ReviewItem          # corrected gold, override logged
POST /review/{id}/reject              -> ReviewItem          # negative example
GET  /conflicts                       -> Page[Conflict]      # Phase 2
POST /conflicts/{id}/resolve          -> Conflict            # resolves whole set

# Search ----------------------------------------------------------------------
GET  /search?q=&limit=&sim_floor=     -> [SearchHit]         # ACL-filtered to caller
POST /context                         -> AssembledContext    # Phase 3 GAG

# Source drilldown ------------------------------------------------------------
GET  /sources/{id}                    -> SourceItemDetail    # versions, normalized text, derived records
GET  /sources/{id}/versions           -> [SourceVersion]
GET  /extractions/{id}                -> ExtractionDetail    # payload + evidence spans + provenance edges

# Cost dashboard --------------------------------------------------------------
GET  /cost?from=&to=&group_by=        -> CostBreakdown       # per tenant/source/job from cost_events
```

### Key interfaces / types

```python
def get_principal(request: Request) -> Principal: ...        # FastAPI dependency (authN)

class ReviewItemDetail(BaseModel):
    extraction: ExtractionOut
    evidence: list[EvidenceSpanOut]      # rendered next to source text
    source: SourceRefOut
    provenance: list[EdgeOut]

class CostBreakdown(BaseModel):
    total_usd: Decimal
    by_group: dict[str, Decimal]         # group_by in {tenant, source, job, model}
    window: tuple[date, date]
```

### Dependencies

- **Review Layer** — review queue, decisions, conflict resolution.
- **Query Layer** — search, context assembly, ACL-resolved principal.
- **Pipeline / Connector** — sync status, reconcile triggers (read `jobs` / `change_events`).
- **Storage Layer** — `cost_events` for the dashboard; source drilldown reads.

### Implementation notes / gotchas

- **Keep it thin.** No promotion logic, no ACL math in routes — resolve the principal and delegate. Business logic belongs in Layers 5/6.
- **AuthZ is the Query Layer's job, always per-request.** The API resolves *who* is asking; the Query Layer decides *what they may see*. Never let a route bypass ACL filtering.
- **Evidence next to source is the headline review UX.** `ReviewItemDetail` must return spans renderable against the exact normalized text the offsets reference.
- **Sync status must distinguish "wedged" from "quiet."** Surface dead-letter count, cursor lag, and last-successful-reconciliation — a stuck connector cannot look healthy.

---

## Implementation Order

### Build order (single engineer, Phase 1)

1. **Storage Layer first — it blocks everything.** Schema, migrations, enums, repositories, `Uow`. Nothing above compiles without typed tables. Get `is_current` partial-uniques, the fingerprint unique index, and the cost/jobs tables right now — they are painful to retrofit. **(Foundational; no parallelism possible.)**

2. **Pipeline Layer (queue skeleton) + Connector Layer (Notion) together.** The queue (`SKIP LOCKED`, enqueue/claim/complete/fail, dead-letter) and the Notion connector (`fetch`, `incremental_scan`, sync state, content hashing, reconciliation) are the plumbing the rest rides on. Build `fetch → normalize → chunk → embed` end-to-end on stub content before any AI. This proves incremental sync — the differentiator — independent of extraction quality.

3. **Extraction Layer.** With chunks flowing, add the Claude `extract` handler: `extraction.v1` schema, Pydantic validation, offset-first span verifier, fingerprinting, cost rows. This is the highest-risk *quality* work; isolate it behind the validated `ExtractionResult` so a bad prompt never corrupts storage.

4. **Review Layer.** Once extractions exist, add the review lifecycle + audit trail + Phase-1 promotion (human confirm → Gold; **no auto-promotion**). This makes "Gold" mean something.

5. **Query Layer (search + ACL).** pgvector HNSW search pinned to one `model_version`, with ACL-filter-before-rank. Phase 1 uses captured principal lists (single connector); live group resolution lands with the second connector.

6. **API Layer.** FastAPI routes over Layers 5/6 + sync/cost reads. Thin; fast to build once the layers beneath are real.

### What can be built in parallel

- **After Layer 1 lands**, two tracks can run concurrently:
  - **Track A (data plumbing):** Connector + Pipeline (steps 2) — owned by an engineer comfortable with messy APIs and queue semantics.
  - **Track B (intelligence):** Extraction Layer (step 3) can be developed against *fixed sample normalized documents* (golden eval set) without waiting for the live connector — its only hard dependency is the `normalized_documents` shape from Layer 1.
- **Review (4) and the search half of Query (5)** can proceed in parallel once extractions exist, since they touch disjoint tables (`review_items`/`conflicts` vs. `embeddings`).
- **API (6)** routes can be stubbed against typed response models early and wired to real services as each lands.

### Why this order

- **Storage is the universal dependency** — every other layer's interface is phrased in its types.
- **Sync-before-extraction** proves the incremental cascade (the product's actual differentiator) cheaply, before spending on AI quality.
- **Extraction behind a validation boundary** means quality iteration never threatens data integrity.
- **Review before any automation** enforces the Phase-1 trust posture: Gold is earned by humans, full stop.
- **Query and API last** because they are read-side and thin — they expose value that must already exist beneath them.

The cross-cutting invariants — **ACL propagation** (columns in Layer 1, union-of-denies in Extraction, enforcement in Query) and **cost accounting** (`cost_events` from Layer 1, one row per Claude call in Extraction, dashboard in API) — are wired in from day 1 in each layer, never bolted on.
