# AGENTS.md — working in the Cognitio repo

This file orients an AI agent (or a new human) so they can start implementing **any layer**
immediately. Read it before touching code. For the *why* read [`DESIGN.md`](./DESIGN.md); for the
*how, layer by layer* read [`ARCHITECTURE.md`](./ARCHITECTURE.md).

## What this repo is

Cognitio is a **source-backed extraction and review system with semantic search**. Content is pulled
from source systems (Phase 1: Notion), normalized with stable character offsets, run through Claude
to produce typed, **evidence-backed** extraction records, reviewed by humans into "Gold" knowledge,
and served via ACL-filtered semantic search. The differentiator is the **incremental sync cascade**:
when a source changes, only the affected chunks are re-extracted, per-record.

This repository is currently a **skeleton**. Types, interfaces, table definitions, and docstrings are
real; method bodies raise `NotImplementedError` and are marked with `# TODO(Phase N):`. Your job when
picking up a task is to fill in a slice without breaking the layering contract.

## The layering contract (read this twice)

Seven layers, one Python package each. **Dependencies point downward only.** A higher layer may call
the public interface of any layer below it; **no layer ever calls upward**, and no layer reaches
around Storage to run ad-hoc SQL.

| # | Layer | Package | Owns |
|---|-------|---------|------|
| 7 | API | `packages/api` (`cognitio_api`) | FastAPI routes; resolves the principal; delegates to 5/6. No business logic. |
| 6 | Query | `packages/query` (`cognitio_query`) | Semantic search, GAG traversal, **ACL enforcement before ranking**. |
| 5 | Review | `packages/review` (`cognitio_review`) | Review lifecycle, promotion rules, conflict/dispute lifecycle. |
| 4 | Extraction | `packages/extraction` (`cognitio_extraction`) | Claude wrapper, `extraction.v1` schema, span verifier, fingerprints, cost rows. |
| 3 | Pipeline | `packages/pipeline` (`cognitio_pipeline`) | `SKIP LOCKED` job queue, worker loop, job stages, invalidation. |
| 2 | Connector | `packages/connectors` (`cognitio_connectors`) | Capability-aware sync contract; Notion connector; sync state. |
| 1 | Storage | `packages/storage` (`cognitio_storage`) | Postgres schema, migrations, typed repositories, `Uow`. |

The runnable process is `apps/worker` (`cognitio_worker`), which wires Pipeline + Connector +
Extraction together into the worker loop.

Two **cross-cutting invariants** are not layers — they are threaded through several layers and must
never be bolted on later:

- **ACL propagation** — columns in Storage (`acl`, `acl_snapshot`, `effective_acl`), union-of-denies
  computed in Extraction, enforced in Query *before ranking and before any content reaches a prompt*.
- **Cost accounting** — `cost_events` table in Storage, exactly one row per Claude call written by
  Extraction, aggregated by the API cost dashboard.

## Where things live (conventions)

- **All SQLAlchemy models** live in `packages/storage/src/cognitio_storage/models.py`. There is one
  model per table from `ARCHITECTURE.md` → Layer 1 Schema. Nothing above Storage defines a table.
- **All migrations** live in `packages/storage/src/cognitio_storage/migrations/` (Alembic).
  Migrations are **forward-only and never edited once shipped**.
- **DB access from any layer goes through repositories and the `Uow`** in `cognitio_storage.db` /
  the repo modules. No raw SQL above Layer 1. If you need a new query, add a repository method.
- **Domain/transport types** that cross a layer boundary are defined in that layer's package
  (e.g. connector types in `cognitio_connectors.base`, the extraction schema in
  `cognitio_extraction.schema`). Pydantic for anything validated or serialized; `@dataclass(frozen=True)`
  for internal value objects.
- **Every row carries `tenant_id`.** Every query is implicitly scoped by tenant. Interfaces omit it
  from signatures for brevity, but it is a mandatory predicate and a non-null column.
- **`is_current` is a database guarantee** (partial unique index), not app logic. "Current-only"
  reads never traverse `supersedes` chains.

## How to do common tasks

### Add a new job type (Pipeline Layer)

1. Add a handler module under `packages/pipeline/src/cognitio_pipeline/jobs/<name>.py` implementing
   the `JobHandler` protocol (`type: str`, `async def run(self, job, tx) -> list[NewJob]`).
2. Register it in the handler registry in `cognitio_pipeline/worker.py`.
3. If it is part of the cascade, document its input payload and follow-on `enqueue`s in the
   job-DAG table in `packages/pipeline/README.md` and emit follow-ons via `complete(job, enqueue=[...])`
   — **completion and follow-on enqueue happen in one transaction**.
4. Make the handler idempotent: writes keyed on a deterministic id (fingerprint / dedupe_key) so a
   re-run is a no-op.

### Add a new connector (Connector Layer)

1. Create `packages/connectors/src/cognitio_connectors/<source>/connector.py` implementing the
   `Connector` protocol from `cognitio_connectors.base`.
2. Declare honest `ConnectorCapabilities` — what the source API actually supports (incremental
   cursor, tombstones, permission metadata, stable hashes). The Pipeline adapts its strategy to them.
3. Persist sync state through `cognitio_connectors.sync_state` (cursors + high-watermark, backed by
   `change_events`). `high_watermark` must advance even when a scan returns no items.
4. Capture the source ACL at fetch time into `source_versions.acl_snapshot`. Permission changes are
   content-invisible changes — they still produce a `change_event` and re-fetch.

### Add / change a table (Storage Layer)

1. Edit `cognitio_storage/models.py`.
2. Generate a migration: `cd packages/storage && uv run alembic revision --autogenerate -m "..."`.
3. Review the generated SQL by hand (autogenerate misses partial indexes, enums, generated columns,
   pgvector HNSW indexes — add them explicitly).
4. Never edit a shipped migration; add a new forward migration instead.

### Run migrations

```bash
cd packages/storage
uv run alembic upgrade head        # apply
uv run alembic downgrade -1        # roll back one (dev only)
```

## Key files to read first, per layer

- **Storage:** `models.py` (every table), then `db.py` (engine, session, `Uow`), then `migrations/env.py`.
- **Connectors:** `base.py` (the `Connector` protocol + all sync types), then `notion/connector.py`,
  then `sync_state.py`.
- **Pipeline:** `worker.py` (claim loop + handler registry), then `queue.py` (enqueue/claim/complete/
  fail), then `jobs/*.py` in cascade order: `fetch → normalize → chunk → embed → extract →
  entity_resolve`, plus `invalidate.py`.
- **Extraction:** `schema.py` (`extraction.v1` Pydantic models — the contract), then `validator.py`
  (span verifier), `prompt.py`, `client.py` (Claude + cost), `fingerprint.py`.
- **Review:** `queue.py` (lifecycle), `promotion.py` (the gate — **no auto-promote in Phase 1**),
  `conflicts.py` (Phase 2 dispute lifecycle).
- **Query:** `search.py` (pgvector pinned to one model_version), `acl.py` (live group resolution),
  `graph.py` (Phase 3 GAG).
- **API:** `main.py` (app + principal dependency), then `routes/*.py`.

## Phase posture (don't build ahead of the phase)

- **Phase 1 (now):** Notion connector + incremental sync + validated extraction + manual Gold
  curation + semantic search + ACL via captured principal lists. **No conflict detection. No
  auto-promotion.** Code for later phases is stubbed and marked `# TODO(Phase 2/3)`.
- **Phase 2:** contradiction detection, gated auto-promotion (low-risk facts only), second connector,
  cross-source identity mapping + live group resolution, hardened entity resolution.
- **Phase 3:** Graph-Augmented Generation (bounded typed traversal), Tier 4 synthesis.

Decisions that constrain you are recorded in [`adr/`](./adr/) — read them before reversing one.

## Conventions & tooling

- Python 3.12, fully typed (`mypy --strict`), `ruff` for lint/format (line length 100).
- Async everywhere below the API; repositories and handlers are `async`.
- Tests with `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`).
- Managed with **uv workspaces**; `uv sync` from the root installs all members. Run any member's
  entry point with `uv run <command>`.
- Stub bodies raise `NotImplementedError`; mark unfinished work with `# TODO(Phase N): ...` so a grep
  for `TODO(Phase` enumerates remaining work.

## Running locally

See [`README.md`](./README.md) → Quick start. Short version: `uv sync`, start a `pgvector/pgvector:pg16`
container, `alembic upgrade head` from `packages/storage`, then `uv run cognitio-worker` and
`uv run uvicorn cognitio_api.main:app --reload`.
