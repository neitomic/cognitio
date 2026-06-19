# Phase 0 (Infra) — Code Review

**Scope:** Tasks 1–5 of `NEXT_STEPS.md` ("Phase 0: Infra"), reviewed against each task's
acceptance criteria, plus general quality/security checks.

**Reviewed tree:** Phase 0 was merged to `main` via PR #1 (`274dde0 Merge pull request #1 from
neitomic/phase/0-infra`), tip `56932e6` (`fix: isolate TEST_DATABASE_URL from unit test defaults
check`). The brief's "Phase 0 already merged" is confirmed.

**Verification run locally:**
- `uv sync --frozen` → success (57 packages).
- `ruff check .` → clean; `ruff format --check .` → 56 files already formatted.
- `mypy` (strict) → no issues in 50 source files.
- `pytest -m unit` → **39 passed, 2 deselected** (integration).
- Integration suite not run here (no Docker/Postgres in review env); skip path verified by code.

**Verdict:** Phase 0 is in good shape — every task's core acceptance is met and all local gates are
green. The blockers below are mostly *spec/docs inconsistencies* and *Phase-1-readiness gaps* in the
test harness, not defects in the shipped Phase 0 code.

---

## ⛔ Blocking / must-resolve before (or at the start of) Phase 1

### B1. Boundary check (task 5) encodes a layering that contradicts `AGENTS.md`
`tests/test_boundaries.py` `LAYER_RANK` and the `AGENTS.md` layer table **disagree on the legal
import direction** for Pipeline vs Extraction/Review/Query:

| Package | `AGENTS.md` layer # | `test_boundaries` rank |
|---|---|---|
| Extraction | 4 | 3 |
| Query | 6 | 4 |
| Review | 5 | 5 |
| Pipeline | 3 | 6 |

`AGENTS.md` ("a higher layer may call any layer below it") would **permit** `Query → Pipeline` /
`Review → Pipeline` and **forbid** `Pipeline → Extraction`. The boundary test enforces the exact
opposite: Pipeline is ranked *above* Extraction/Query/Review, so it allows `Pipeline → Extraction`
and forbids `Query → Pipeline`.

The test is the architecturally-correct one — it matches the actual declared dependencies
(`cognitio-pipeline` depends on `cognitio-extraction`, "an orchestrator ranks above what it
orchestrates," as its own docstring explains). So **`AGENTS.md`'s table is the artifact that is
wrong/misleading.** Task 5's acceptance literally says the check must reject upward imports
"according to `AGENTS.md`," and right now they conflict. A Phase 1 dev who trusts the `AGENTS.md`
table will build the wrong mental model (e.g. think Pipeline may import Query). **Action:** correct
the `AGENTS.md` layer table (and the identical diagram in `README.md`) so the documented numbering
matches the enforced ranking, or annotate the orchestrator exception explicitly.

### B2. Test harness has no schema/session bootstrap — Phase 1 integration tests can't write rows yet
`conftest.py` provides `db_engine` and `db_connection` (a raw `AsyncConnection` wrapped in a
rolled-back transaction). That is enough for the current "SELECT 1 / check extensions" smoke tests,
but it is **not** enough for Phase 1 (tasks 6, 10–13), which need to read/write tenant-scoped rows:

- No fixture creates the schema. The test DB is empty — there are no tables and (in Phase 0) no
  Alembic migrations. Phase 1 repository integration tests will need a fixture that runs
  `alembic upgrade head` (preferred — also exercises the migration) or `Base.metadata.create_all`
  against the test connection.
- No `AsyncSession` / `Uow` fixture exists (expected — `db.py` lands in task 6), but the rollback
  isolation in `db_connection` is connection-level; Phase 1 will want a session bound to that same
  connection so repository code and the test share one rolled-back transaction.
- Factories build **in-memory domain value objects** (connector/extraction/pipeline types), not ORM
  rows. Row-insertion helpers depend on `models.py`, which doesn't exist yet.

None of this is a Phase 0 defect — but it is the first wall Phase 1 hits, so plan task 6/10 to
extend the harness with a migrated-schema fixture and a session-per-test fixture.

---

## ⚠️ Should-fix (non-blocking, but cheap and worth doing)

### S1. `just ci` does not match what CI actually runs
- `Justfile`: `ci: lint type test` — **no integration tests.**
- `.github/workflows/ci.yml`: runs lint, type, unit, **and** the pgvector integration suite.
- `README.md` claims `just ci` is "the same gates CI runs" — inaccurate.

Task 5's acceptance is explicitly "CI runs the same commands documented for local development." Today
a green `just ci` does not reproduce CI. **Fix:** either add a guarded integration step to `just ci`
(e.g. a `ci-int` that runs `test-int` when `TEST_DATABASE_URL` is set) or change the README wording
to "lint + type + unit (integration runs in CI)."

### S2. CI has no migration gate yet
Task 5's acceptance lists failing "on lint, type, migration, or tests." There is no migration step
(correct — Alembic arrives in task 10). Flagging so it isn't forgotten: when task 10 lands, add an
`alembic upgrade head` (and a dev-only downgrade/re-upgrade) step to `ci.yml`, otherwise the
"migration" half of the acceptance is never enforced.

### S3. Settings exist but no composition root constructs them
Task 2's acceptance: "API and worker can construct settings without reading environment variables
directly." The `Settings`/`get_settings()` mechanism is solid and unit-tested, but **neither**
composition root actually uses it yet: `cognitio_worker.main.run()` is a `NotImplementedError` stub
and `cognitio_api.main` builds the app with no settings reference. This is deferred to task 36 by
design, so it's acceptable — but the task-2 acceptance is only *half* met (the capability exists; it
is not yet wired). Worth a note so it isn't assumed "done."

### S4. Shallow smoke imports
`test_every_package_imports` does `importlib.import_module(<top-level package>)` only. Several
`__init__.py` files are thin (e.g. `cognitio_storage` re-exports just `enums`), so a broken
*submodule* would not be caught by this test. `mypy --strict` over all `src/` files is the real
safety net here, which mitigates it — but the "smoke test imports all packages" guarantee is weaker
than it reads.

### S5. `extra="ignore"` can silently swallow mistyped secret env vars
`SettingsConfigDict(extra="ignore")` means a typo'd variable in `.env` (e.g. `ANTHROPC_API_KEY=`)
is silently dropped, leaving the real field at its `None` default — a confusing "missing credential"
failure later. Low severity; consider `extra="forbid"` for the app's own namespace, or document the
risk.

---

## ℹ️ Minor / nits

- **`README.md` step numbering skips 5** (jumps from "### 4. Start Postgres" to "### 6. Run
  migrations"). Cosmetic.
- **Spec said "root `tests/conftest.py`"** (task 4); the file is actually at the **repo root**
  (`./conftest.py`). This is arguably better (it also covers `packages/` and `apps/` test dirs) — no
  action needed, just noting the deviation from the literal wording.
- **No assertion that `TEST_DATABASE_URL != DATABASE_URL`.** A fat-fingered `.env` could point the
  rollback-isolated integration tests at the dev DB. Cheap guard worth adding when the harness grows.
- **`openai` is not a dependency** anywhere, yet `embedding_provider` defaults to `"openai"`. Fine
  for Phase 0 (unused); the adapter + dep land in task 29.

---

## ✅ Verified good (explicit passes)

- **Task 1 — workspace/lockfile:** `uv sync --frozen` succeeds from the committed `uv.lock`; every
  package imports (smoke test + 39 unit tests pass). Workspace members, `[tool.uv.sources]`, and the
  root meta-package (`package = false`) are correctly configured. **MET.**
- **Task 2 — settings:** All required fields present (DB URLs, Notion token/roots, Anthropic
  key/model, embedding provider/model-version, fallback ACL, worker timing). Secrets use `SecretStr`;
  a dedicated test proves redaction in `repr`/`str`. Required-field, default, invalid-URL, CSV-list,
  and worker-bound validation all unit-tested. `.env.example` documents every variable. **MET**
  (wiring caveat in S3).
- **Task 3 — Postgres infra:** `compose.yaml` uses `pgvector/pgvector:pg16`, a named persistent
  volume, a healthcheck that gates on **both** `cognitio` and `cognitio_test` being ready, and
  `docker/initdb` scripts that create the test DB and enable `vector` + `pgcrypto` in **both**
  databases. README documents up/down/reset (via `Justfile`). Config is correct by inspection
  (Docker not run in review env). **MET.**
- **Task 4 — test harness/factories:** Markers `unit`/`integration`/`live` declared with
  `--strict-markers`; unmarked tests auto-default to `unit`. Integration fixtures **skip with an
  actionable message** when `TEST_DATABASE_URL` is unset (unit suite needs no Docker/creds — proven).
  Factories cover tenants, ACLs, source snapshots, normalized docs/chunks, extraction envelopes, and
  jobs, all built from the **real** package types and round-trip-tested. **MET** (Phase-1 extension
  needed per B2).
- **Task 5 — boundaries + CI:** AST-based upward-import detector with positive *and* negative
  self-tests; smoke imports of all 9 packages; `ci.yml` runs frozen install, ruff check + format,
  mypy, unit, and pgvector-backed integration, excluding `live`. **Largely MET** (caveats B1, S1,
  S2).

### Security
- **No secrets in source.** `.env` is git-ignored; `.env.example` ships empty secret values; secrets
  are `SecretStr` and redaction is tested. **PASS.**
- **`.gitignore` is adequate** — covers `.venv/`, `__pycache__/`, `*.py[cod]`, `.mypy_cache/`,
  `.pytest_cache/`, `.ruff_cache/`, and `.env`. `git ls-files` confirms **no** cache/junk/`.env`
  files are tracked. (The cache dirs exist on disk but are correctly untracked.) **PASS.**
- **compose dev-safety:** trivial `cognitio:cognitio` credentials and host-published `5432` are
  acceptable for a **local dev** stack (which is all this is), but must never be promoted to a shared
  or production environment. `restart: unless-stopped` + named volume are reasonable dev defaults.

---

## Bottom line for Phase 1 kickoff

1. **Reconcile the `AGENTS.md` layer table with the enforced boundary ranking** (B1) before anyone
   writes Phase 1 imports against the wrong contract.
2. **Plan the harness extension** (B2): a migrated-schema fixture + session-per-test fixture are
   prerequisites for the task 6/10–13 repository integration tests.
3. Tidy `just ci` ↔ CI parity (S1) and add the migration gate when task 10 lands (S2).
