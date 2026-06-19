# Cognitio

A **living knowledge platform** for companies. Where static RAG indexes documents once and drifts
out of date, Cognitio maintains a continuously updated, tiered knowledge graph: content from
documents, discussions, and comments flows in incrementally and is progressively distilled into
higher-quality, structured knowledge.

At its core Cognitio is a **source-backed extraction and review system with semantic search**. The
hard, differentiating work is building trustworthy extraction records with exact provenance, keeping
connectors synchronized despite messy source APIs, and making review efficient enough that "Gold"
knowledge means something.

- **What & why:** [`DESIGN.md`](./DESIGN.md)
- **How (layer by layer):** [`ARCHITECTURE.md`](./ARCHITECTURE.md)
- **Working in this repo (agents & humans):** [`AGENTS.md`](./AGENTS.md)
- **Decisions:** [`adr/`](./adr/)

## Architecture at a glance

Seven independently implementable layers, dependencies pointing **downward only**:

```
7. API Layer            FastAPI — sync, review, search, cost   packages/api
6. Query Layer          semantic search · GAG · ACL @ query     packages/query
5. Review Layer         review lifecycle · promotion · audit    packages/review
4. Extraction Layer     Claude · extraction.v1 · evidence       packages/extraction
3. Pipeline Layer       job queue (SKIP LOCKED) · workers       packages/pipeline
2. Connector Layer      capability-aware sync · Notion          packages/connectors
1. Storage Layer        Postgres + pgvector · typed tables      packages/storage
```

Two cross-cutting invariants are threaded through the layers, never bolted on: **ACL propagation**
(union-of-source-denies) and **per-tenant cost accounting**.

The runnable process lives in `apps/worker` (the pipeline worker loop). The API is served from
`packages/api` via `uvicorn`.

## Tech stack

PostgreSQL ≥ 16 + `pgvector` + `pgcrypto` · Python 3.12 async · Postgres `SKIP LOCKED` queue ·
Claude (Haiku `claude-haiku-4-5-20251001` / Sonnet `claude-sonnet-4-6`) structured outputs ·
`text-embedding-3-small` · FastAPI · SQLAlchemy + Alembic · managed with **uv** workspaces.

## Quick start

> The repository is a **skeleton**: types, interfaces, and stubs are in place; method bodies raise
> `NotImplementedError`. The commands below describe the intended local workflow.

### 1. Prerequisites

- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Docker (for local Postgres) or a Postgres ≥ 16 with `pgvector` + `pgcrypto`
- [`just`](https://just.systems/) (optional, recommended — wraps the commands below)
- An Anthropic API key (`ANTHROPIC_API_KEY`) for the extraction layer

### 2. Install the workspace

```bash
uv sync                       # resolves and installs every workspace member
just sync                     # equivalent
```

### 3. Configure the environment

```bash
cp .env.example .env          # then fill in tokens/keys
```

Settings are read by `cognitio_config.Settings` (see `packages/config`). The example file
documents every variable, including `DATABASE_URL` and `TEST_DATABASE_URL`.

### 4. Start Postgres (compose)

`compose.yaml` runs `pgvector/pgvector:pg16` with a persistent dev database (`cognitio`) and a
disposable test database (`cognitio_test`); both get the `vector` and `pgcrypto` extensions on
first start.

```bash
just up           # docker compose up -d   — start (waits until healthy)
just down         # docker compose down    — stop (keeps the data volume)
just reset-db     # drop & recreate the dev database, then re-create extensions
```

Connection URLs (already set in `.env.example`):

```bash
DATABASE_URL=postgresql+asyncpg://cognitio:cognitio@localhost:5432/cognitio
TEST_DATABASE_URL=postgresql+asyncpg://cognitio:cognitio@localhost:5432/cognitio_test
```

### 6. Run migrations

> Alembic configuration and the initial migration land in Phase 1 (`packages/storage`).

```bash
cd packages/storage
uv run alembic upgrade head
```

### 7. Run the worker and the API

```bash
# Pipeline worker (claims jobs with SKIP LOCKED, runs the cascade)
uv run cognitio-worker

# API (FastAPI) in another shell
uv run uvicorn cognitio_api.main:app --reload
```

## Development & testing

Common tasks are wrapped in the [`Justfile`](./Justfile) (run `just` to list them):

```bash
just lint      # ruff check + ruff format --check
just fmt       # ruff format
just type      # mypy (strict, shipped source)
just test      # unit tests (no Docker, no credentials)
just test-int  # integration tests (require Postgres via TEST_DATABASE_URL)
just ci        # lint + type + test — the same gates CI runs
```

Tests use three markers (`unit`, `integration`, `live`). The default suite is `unit`; integration
tests skip with an actionable message when `TEST_DATABASE_URL` is unset, and `live` tests
(real provider calls) are opt-in only. CI (`.github/workflows/ci.yml`) runs the frozen install,
lint, type check, the unit suite, and the pgvector-backed integration suite.

## Repository layout

```
cognitio/
├── DESIGN.md            what the system is and why
├── ARCHITECTURE.md      how to build it, layer by layer
├── AGENTS.md            how to navigate & contribute (read this first)
├── adr/                 Architecture Decision Records
├── packages/            the seven layers, one package each
│   ├── storage/         Layer 1 — schema, migrations, repositories
│   ├── connectors/      Layer 2 — capability-aware sync (Notion)
│   ├── pipeline/        Layer 3 — job queue + worker + job stages
│   ├── extraction/      Layer 4 — Claude wrapper, extraction.v1, verifier
│   ├── review/          Layer 5 — review lifecycle, promotion, conflicts
│   ├── query/           Layer 6 — semantic search, GAG, ACL enforcement
│   └── api/             Layer 7 — FastAPI routes
└── apps/
    └── worker/          worker process entrypoint
```

## License

Proprietary — internal project skeleton.
