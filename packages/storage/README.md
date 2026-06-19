# cognitio-storage — Layer 1, Storage

The foundation. Owns the Postgres schema, migrations, and **typed access to every table**. Every
other layer reaches durable state *only* through this layer's repository functions — no ad-hoc SQL
above Layer 1.

## What lives here

| File | Purpose |
|------|---------|
| `models.py` | SQLAlchemy 2.0 models — one per table in `ARCHITECTURE.md` → Layer 1 Schema. |
| `types.py` | Declarative `Base`, shared column types, and tenant-safe composite FK helpers. |
| `db.py` | Async engine, session factory, and the `Uow` (unit-of-work) transactional primitive. |
| `repositories/` | Typed, tenant-scoped repository classes (CRUD + domain queries) per table group. |
| `migrations/` | Alembic environment (`env.py`, async) + versioned, **forward-only** migrations. |

## Responsibilities

- Define the schema: tables, columns, enums, constraints, indexes (including the `is_current` partial
  uniques, the fingerprint unique index, and the per-version HNSW vector indexes).
- Own migrations and the `pgvector` / `pgcrypto` extensions.
- Provide typed repositories and the `Uow` used by the Pipeline Layer for atomic multi-row commits.
- Enforce the structural invariants the DB *can* enforce (NOT NULL, CHECK, unique, partial-unique).

## Gotchas (see ARCHITECTURE.md → Layer 1)

- **`is_current` uniqueness is a partial unique index, not app logic.** "Current-only" reads never
  traverse `supersedes`.
- **`edges` has no FKs by design** — it spans every node type; integrity is an app invariant kept by
  the orphan-GC job in the Pipeline Layer. Do not add FKs.
- **Prefer explicit writes** of the promoted columns (`owner_entity_id`, `due_date`, …) over
  `GENERATED ... STORED` from `jsonb` — generated-from-jsonb expressions are brittle across versions.
- **HNSW is one space per index** — never mix `model_version` in a single index; the per-version
  partial index is the blue/green re-embed mechanism.
- **Crypto-shredding lives in the column design** — `raw_content` is encrypted with a per-record key
  (`enc_key_id`); right-to-deletion destroys the key, not the row.
- **Migrations are forward-only and reviewed.** Never edit a shipped migration.

## Running migrations

```bash
uv run alembic upgrade head            # apply all
uv run alembic revision --autogenerate -m "describe change"
uv run alembic downgrade -1            # dev only
```

`alembic.ini` reads the database URL from the `DATABASE_URL` environment variable (see
`migrations/env.py`).
