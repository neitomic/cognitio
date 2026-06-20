# PR #2 Review — Phase 1: Storage + Connectors

Verdict: **request changes**. The implemented subset is clean and passes its tests, but tasks 8,
9, 11, 12, and 13 are materially incomplete, and the Notion pre-fetch acceptance criterion is not
implemented end to end.

## Blockers

1. **Required Phase 1 tables are missing from both the models and initial migration.**
   - Task 8 requires `cost_events` plus searchable text/`tsvector` support on normalized documents
     and extractions. None are present.
   - Task 9 requires `jobs`, `entity_merges`, `access_audit_events`, and
     `blocking_signatures`. None are present.
   - A clean migrated database contains only 17 application tables:
     `change_events`, `conflicts`, `connector_scan_items`, `connector_sync_states`, `edges`,
     `embeddings`, `entities`, `entity_mentions`, `extractions`, `normalized_chunks`,
     `normalized_documents`, `principals`, `review_items`, `source_acl_rules`, `source_items`,
     `source_versions`, and `tenants`.
   - The metadata test's `ALL_TABLES` set omits the same required tables, so it cannot catch the
     gap (`packages/storage/tests/test_models_metadata.py:20`).

2. **Repository tasks 11–13 are incomplete.**
   - There are no repositories for jobs, costs, conflicts, entity merges, blocking signatures,
     source ACL rules, access audits, or entity mentions.
   - Task 11's `archive_missing` operation is absent. `ConnectorScanRepository.missing_since`
     only returns IDs and does not enforce the “completed scan only” safety condition
     (`packages/storage/src/cognitio_storage/repositories/sync.py:132`).
   - Task 12 requires document/chunk diff operations and extraction insert/version/stale/archive/
     searchable-text operations. Only document insert/get, chunk hash listing, extraction insert,
     stale, and trust updates exist. Extraction versioning, archive, searchable-text updates, and a
     typed chunk diff are absent.
   - Task 13's queue, conflict, blocking-signature, cost, and immutable access-audit repositories
     are absent.
   - Repositories are not exposed through `Uow`; `Uow.__aenter__` returns a bare `AsyncSession`
     (`packages/storage/src/cognitio_storage/db.py:48`).

3. **The Notion pre-fetch gate is not connected to the fetch path.**
   - `NotionConnector.needs_fetch` exists only as a static helper
     (`packages/connectors/src/cognitio_connectors/notion/connector.py:105`).
   - `fetch()` always calls `retrieve_page` and `_collect_blocks`
     (`packages/connectors/src/cognitio_connectors/notion/connector.py:120`); it receives no
     recorded timestamp/checkpoint and never calls `needs_fetch`.
   - The test checks the helper's Boolean result but never proves that an unchanged page avoids a
     block-tree request (`packages/connectors/tests/test_notion_connector.py:153`).
   - Therefore the explicit acceptance criterion “unchanged `last_edited_time` skips the
     block-tree fetch” is not met.

4. **Persisted `supports`/`contradicts` enforcement does not match DESIGN.md.**
   - Minimum confidence floors (`supports >= 0.7`, `contradicts >= 0.8`) are not enforced.
     `confidence=None` is accepted by `EdgeRepository.insert`
     (`packages/storage/src/cognitio_storage/repositories/edges.py:55`).
   - When a cap is reached, the implementation raises instead of retaining/replacing the strongest
     edges.
   - The repository's count-then-insert is race-prone, and the trigger also performs an unlocked
     `count(*)`; concurrent writers can all observe space below the cap and commit above it
     (`packages/storage/src/cognitio_storage/migrations/versions/0001_initial.py:37`).
   - The integration test covers only sequential `contradicts` inserts through the repository. It
     does not test `supports`, confidence floors, strongest-edge retention, the database trigger,
     or concurrent writers.

5. **The initial migration is not a frozen schema revision.**
   - `0001_initial.py` imports live ORM metadata and runs `Base.metadata.create_all()` /
     `drop_all()` (`packages/storage/src/cognitio_storage/migrations/versions/0001_initial.py:68`,
     `:89`).
   - Any later model edit silently changes what revision `0001_initial` creates and drops. That
     defeats Alembic's forward-only migration history and can make identical revision IDs produce
     different schemas across environments.
   - Replace this with explicit Alembic operations for the complete initial schema, enums,
     constraints, and indexes.

## Should fix

1. **Notion capabilities and incremental behavior are overstated.**
   - `updated_since_filter=True` is declared, but the HTTP search request supplies only a sort and
     object filter; it never sends an updated-since predicate
     (`packages/connectors/src/cognitio_connectors/notion/client.py:82`).
   - `_in_scope` recognizes configured roots and immediate children only, not an arbitrary page
     subtree (`packages/connectors/src/cognitio_connectors/notion/connector.py:184`).
   - `source_revision` is epoch seconds only. ARCHITECTURE.md explicitly requires
     `last_edited_time + fetch sequence` because Notion timestamps are second-rounded; two edits in
     one second can collapse to the same revision.

2. **Quiet/empty Notion scans do not necessarily advance a high-watermark.**
   - `_max_last_edited([])` returns `None`; the sync repository ignores a `None` high-watermark
     (`packages/storage/src/cognitio_storage/repositories/sync.py:53`).
   - The checkpoint unit/integration tests pass a synthetic non-null high-watermark, so they do not
     cover the actual empty Notion page behavior.

3. **Some relational integrity and consistency constraints are weaker than the design.**
   - `source_items.current_version_id` is an unconstrained UUID despite DESIGN/ARCHITECTURE
     describing it as a source-version FK (`packages/storage/src/cognitio_storage/models.py:191`).
   - Tenant-scoped rows contain `tenant_id` but do not FK it to `tenants`; tests intentionally
     insert arbitrary tenant UUIDs.
   - Gold consistency is enforced in one direction only: Gold requires `gold_source`, but a
     non-Gold extraction may still carry `gold_source`.
   - Chunk spans allow zero-length chunks (`end_char >= start_char`) and do not constrain offsets
     against the document length.

4. **Idempotent writes use read-then-insert and are not concurrency-safe.**
   - `upsert_ref`, source-version insertion, change-event insertion, sync-state creation, scan
     membership, and extraction insertion can race and surface unique violations instead of
     becoming no-ops. Use PostgreSQL `ON CONFLICT` or explicitly handle `IntegrityError` within a
     savepoint.

5. **Source-item upsert is insert-only.**
   - An existing item does not refresh `source_url`, `node_type`, ACL, or lifecycle. Connector
     metadata and reactivated items can remain stale.

6. **Tests cover a real database but not the full acceptance surface.**
   - The integration fixture runs real Alembic migrations and binds repositories to PostgreSQL;
     these are not mocks.
   - Missing coverage includes all omitted tables/repositories, cross-tenant writes/FK rejection,
     incomplete-scan archive safety, duplicate-write races, extraction version/archive behavior,
     job dedupe, immutable audit insertion, open-conflict lookup, edge-trigger enforcement, and
     concurrent edge caps.
   - Notion unit tests use `httpx.MockTransport`, which is appropriate, but do not test an actual
     pre-fetch skip, search pagination, deep subtree scoping, empty-page checkpointing, timeout,
     5xx retry, or same-second revisions.

## Verified good

- `Connector` and `AbstractConnector` contain all method signatures specified by DESIGN.md:
  capabilities, full/incremental scan, fetch, child fetch, and tombstone scan.
- Every implemented application table has a non-null `tenant_id`; implemented parent-child
  relationships use composite tenant-safe FKs. Polymorphic `edges` correctly have no FKs.
- `source_versions` includes `raw_content: bytea` and `enc_key_id: uuid`, plus immutable snapshot
  identity and a partial current-version index. Actual encryption still needs to occur before
  repository insertion.
- Extraction evidence non-empty, current-fingerprint uniqueness, promoted query columns,
  Gold-requires-source, model-version embedding uniqueness, edge lookup indexes, and current-row
  partial indexes are represented in metadata.
- Async Alembic execution is wired correctly through `AsyncEngine` and `run_sync`.
- On a disposable pgvector/PostgreSQL 16 database, `alembic upgrade head`, `downgrade -1`, and
  re-upgrade all completed successfully.
- CI's root-level migration command works with the current `alembic.ini`, and the integration
  fixture exercises the real migration before repository tests.
- Local verification on this branch:
  - `uv sync --frozen` — passed
  - `ruff check .` and `ruff format --check .` — passed
  - `mypy` — passed
  - unit tests — 75 passed
  - PostgreSQL integration tests — 15 passed

