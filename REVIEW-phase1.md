# PR #2 Review — Phase 1: Storage + Connectors (tasks 6–13)

**Verdict: request changes.** The landed subset is clean, typed, and green — migrations apply,
75 unit + 15 integration tests pass against a real pgvector Postgres, and the core tenant-safe
schema is well modelled. But the PR is materially **under-scoped against the task-6–13 acceptance
criteria**: five required tables and their repositories are absent (task 8/9/13), several task-11/12
repository operations are missing, the Notion pre-fetch gate is not wired into the fetch path
(task 13/17), and the edge fan-out guard diverges from DESIGN. Each finding below was verified
against the code and, where checkable, against a live database on this branch.

Live verification (this branch, against `pgvector/pgvector:pg16`):

| Check | Command | Result |
|---|---|---|
| Frozen install | `uv sync --frozen` | ✅ |
| Lint | `ruff check .` / `ruff format --check .` | ✅ (75 files) |
| Types (CI target) | `uv run mypy` | ✅ 62 source files |
| Unit | `pytest -m "not integration and not live"` | ✅ 75 passed |
| Integration | `pytest -m integration` | ✅ 15 passed (real migrated DB) |
| Migration | `alembic upgrade head` → `downgrade -1` → re-upgrade | ✅, creates **17** app tables |

---

## Blockers (task acceptance not met)

### 1. Five required tables are missing from the models and migration (tasks 8, 9)
A freshly migrated database contains 17 application tables. Absent:

- **Task 8** — `cost_events`, and the **searchable text / `tsvector`** surface on extractions and
  normalized documents. `grep` finds no `tsvector`, `to_tsvector`, `pg_trgm`, or any `searchable_*`
  column anywhere in `packages/storage`. The only GIN index is `ix_extr_payload` over the raw JSONB
  payload — not the FTS surface task 8 requires.
- **Task 9** — `jobs`, `entity_merges`, `access_audit_events`, `blocking_signatures`.

The metadata test's `ALL_TABLES` set (`packages/storage/tests/test_models_metadata.py:20`) omits
exactly these tables, so `test_all_tables_present` cannot catch the gap. The test docstring claims
it covers "tasks 7-9".

### 2. Repository tasks 11–13 are incomplete
- **Task 13 has essentially no implementation.** There are no repositories for jobs, costs,
  conflicts, entity merges, blocking signatures, source ACL rules, access audits, or entity
  mentions. Task 13's named acceptance — "job dedupe, … immutable audit insertion, … open-conflict
  lookup" — is untestable because neither the tables nor the repos exist.
- **Task 11** — there is no `archive_missing` operation. `ConnectorScanRepository.missing_since`
  (`repositories/sync.py:132`) returns tombstone *candidates* but never archives, and does not
  enforce the "only after a completed scan" safety condition. Task 11's "incomplete scans never
  archive data" cannot be asserted because no archive path is wired.
- **Task 12** — only `insert_if_absent` / `get` / `current_by_fingerprint` / `by_chunk` /
  `mark_stale` / `set_trust` exist on `ExtractionRepository`. The required extraction
  **versioning / supersession**, **archive**, and **searchable-text** operations, plus a typed
  **chunk diff**, are absent (`hashes_for_document` returns a raw `{id: hash}` map, not a diff).
- Repositories are **not exposed through `Uow`** as task 12 asks. `Uow.__aenter__` yields a bare
  `AsyncSession` (`db.py:48`); callers must construct each repository by hand.

### 3. Notion pre-fetch gate is not connected to the fetch path (task 13 / 17)
- `NotionConnector.needs_fetch(...)` exists only as a **static helper**
  (`notion/connector.py:104`). `fetch()` (`:120`) unconditionally calls `retrieve_page` and
  `_collect_blocks`; it receives no recorded timestamp/checkpoint and never calls `needs_fetch`.
- `test_prefetch_gate_skips_unchanged` (`tests/test_notion_connector.py:156`) asserts only the
  boolean helper's return value. **No test proves a block-tree request is skipped.** The file
  docstring nonetheless claims it "covers … the last_edited_time pre-fetch gate."
- Net: the acceptance criterion "pages with an unchanged `last_edited_time` are skipped without a
  block-tree fetch" is not demonstrated. (Reasonable design seam — the gate belongs in the
  reconcile/fetch handler — but as shipped it is a dead helper, so it should not be claimed done.)

---

## Should fix

### 4. Edge fan-out guard diverges from DESIGN and is not concurrency-safe
DESIGN §"Write-time discipline for materialized supports/contradicts" requires: materialize
`supports` only at confidence ≥ 0.7 and `contradicts` only at ≥ 0.8, and **when the cap is hit,
drop the lowest-confidence edge so only the strongest survive**.

- `EdgeRepository.insert` (`repositories/edges.py:55`) accepts `confidence=None` and enforces **no
  floor**.
- At the cap it **raises `EdgeCapExceeded`** rather than replacing the weakest edge.
- The guard is **count-then-insert** in the repo, and the DB trigger
  (`migrations/versions/0001_initial.py:37`) also does an **unlocked `count(*)`**; concurrent
  writers can each see room below the cap and commit above it.
- `test_edge_fanout_cap_guard` covers only sequential `contradicts` inserts through the repo — no
  `supports`, no floor, no weakest-drop, no trigger path, no concurrency.

The strict floor + weakest-drop behaviour is formally task 34 (Phase 3), so its absence is
acceptable *for this PR* — but the cap should be tracked as a deferral, and the **race** is a
genuine Phase-1 correctness defect in code that ships now.

### 5. Initial migration is not a frozen revision
`0001_initial` calls `Base.metadata.create_all()` / `drop_all()` (`:68`, `:89`). Revision 0001 is
therefore not pinned to a schema snapshot: any later model edit silently changes what 0001 creates,
which defeats forward-only migration history. Task 10 asks for tables/indexes created **"explicitly"**
(only the HNSW index and fan-out trigger currently are). Functionally task 10's acceptance passes
(upgrade/downgrade/re-upgrade verified), so this is maintainability rather than correctness — but
worth fixing before more revisions stack on top.

### 6. Notion connector overstates capabilities / incremental behaviour
- `capabilities()` declares `updated_since_filter=True`, but `search()` (`notion/client.py:83`)
  sends only an object filter + `last_edited_time` sort — no updated-since predicate. The
  capability is dishonest (Notion's search has no such filter; the connector relies on descending
  sort).
- `source_revision` is epoch seconds only (`connector.py:225`). ARCHITECTURE.md:450 explicitly
  requires `last_edited_time + a fetch sequence` because Notion timestamps are second-rounded; two
  edits in one second collapse to the same revision and the monotonic guard then drops the second.
- `_in_scope` (`connector.py:184`) matches configured roots and their **immediate** children only,
  not an arbitrary page subtree as DESIGN describes.

### 7. Idempotent writers are read-then-insert (not concurrency-safe)
`upsert_ref`, `SourceVersion.insert_if_new`, `ChangeEvent.insert_if_new`, scan membership, and
extraction insert all do `SELECT … else INSERT`. Under concurrency these race and surface a unique
violation instead of becoming a no-op. Use `INSERT … ON CONFLICT DO NOTHING/UPDATE` (the embedding
repo already does — good). Relatedly, `upsert_ref` is **insert-only**: an existing item never
refreshes `source_url` / `node_type` / `acl` / `lifecycle`, so reactivated or moved items go stale.

### 8. Minor schema / consistency gaps
- `source_items.current_version_id` is an unconstrained nullable UUID; ARCHITECTURE.md:78 describes
  it as an FK → `source_versions`. Defensible (avoids a circular FK) but integrity is app-only.
- Tenant-scoped rows carry `tenant_id` but **no FK to `tenants`**; tests insert arbitrary tenant
  UUIDs and they persist. Composite tenant-safe FKs between data tables are correct, but the tenant
  root itself is unenforced.
- Gold consistency is one-directional: `gold_needs_source` ensures gold ⇒ `gold_source`, but a
  non-gold row may still carry a stray `gold_source`.
- `chunk_span_ordered` allows zero-length chunks (`end_char >= start_char`) and does not bound
  offsets against document length.
- An empty Notion scan yields `_max_last_edited([]) == None`, and `checkpoint` ignores a `None`
  high-watermark (`sync.py:53`); the "advances on empty" test feeds a synthetic non-null watermark,
  so the real empty-page path is not exercised. Cursor still advances, so this is mild.

---

## Test gaps (tests are real, but acceptance surface is thin)
- ✅ Integration tests run against a **real migrated Postgres** with per-test rollback — not mocks.
  Notion tests use `httpx.MockTransport`, which is the right call.
- Missing: cross-tenant **write/FK rejection** (only read isolation is tested); `supports` edges +
  floors + weakest-drop + DB trigger + concurrent cap; extraction stale/trust read-back and any
  version/archive path; job dedupe and immutable audit (no tables); an actual pre-fetch **skip**;
  search pagination, deep-subtree scoping, empty-page checkpoint, timeout/5xx, and same-second
  revisions. `Uow` commit/rollback (task 6) is covered by `test_db_uow.py` — good.

---

## Verified good
- **Connector contract is exact.** `Connector` (Protocol) and `AbstractConnector` carry every
  signature from DESIGN.md:231 / ARCHITECTURE.md:405 — `capabilities`, `full_scan`,
  `incremental_scan`, `fetch`, `fetch_children`, `tombstone_scan` — with matching types.
- **Tenant scoping is structural.** Every table has a non-null `tenant_id`; parent→child links use
  composite tenant-safe FKs `(tenant_id, x) → (parent.tenant_id, parent.id)`; `edges` correctly
  carry no FKs (polymorphic).
- **Crypto-shred columns present day 1.** `source_versions.raw_content` (bytea) + `enc_key_id`
  (uuid), immutable snapshot identity, and the `one_current_version` partial unique index all
  exist. (Encryption itself is not yet applied — `raw_content` is stored as plaintext bytes; fine
  for a schema-readiness task, but flag for the fetch handler.)
- **Constraints/indexes modelled correctly:** evidence-non-empty + gold-needs-source CHECKs,
  current-only fingerprint uniqueness, promoted query columns, one-embedding-per-object-version,
  per-version HNSW ANN index, edge lookup indexes, and the fan-out trigger.
- **Async Alembic is correct** — `env.py` drives migrations through an `AsyncEngine` +
  `connection.run_sync`, reading `DATABASE_URL`/`TEST_DATABASE_URL`.
- **CI migration step works** — now `uv run alembic -c packages/storage/alembic.ini upgrade head`;
  verified upgrade/downgrade/re-upgrade on a disposable pg16 DB. The integration fixture runs the
  real migration before binding repositories.

## Task-by-task acceptance summary
| Task | Scope | Status |
|---|---|---|
| 6 | base types, `db.py`, `Uow` | ✅ (repos not exposed via `Uow`) |
| 7 | source/sync/normalization tables + crypto-shred | ✅ |
| 8 | extraction/review/embedding tables | ⚠️ `cost_events` + tsvector/FTS missing |
| 9 | queue/entity/conflict/edge/audit tables | ❌ `jobs`, `entity_merges`, `access_audit_events`, `blocking_signatures` missing |
| 10 | initial Alembic migration | ✅ functional; ⚠️ uses `create_all`, not frozen |
| 11 | source/sync repositories | ⚠️ no `archive_missing`; read-then-insert races |
| 12 | doc/chunk/extraction repositories | ⚠️ no extraction version/archive/searchable-text, no chunk diff, not on `Uow` |
| 13 | queue/review/embedding/edge/audit repositories | ❌ only embedding/review/edge present; queue/cost/conflict/audit absent |
