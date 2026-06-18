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
- An Anthropic API key (`ANTHROPIC_API_KEY`) for the extraction layer

### 2. Install the workspace

```bash
uv sync                       # resolves and installs every workspace member
```

### 3. Start Postgres

```bash
docker run -d --name cognitio-pg \
  -e POSTGRES_PASSWORD=cognitio -e POSTGRES_DB=cognitio \
  -p 5432:5432 pgvector/pgvector:pg16
export DATABASE_URL=postgresql+asyncpg://postgres:cognitio@localhost:5432/cognitio
```

### 4. Run migrations

```bash
cd packages/storage
uv run alembic upgrade head
```

### 5. Run the worker and the API

```bash
# Pipeline worker (claims jobs with SKIP LOCKED, runs the cascade)
uv run cognitio-worker

# API (FastAPI) in another shell
uv run uvicorn cognitio_api.main:app --reload
```

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
