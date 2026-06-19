"""Root test configuration: markers, database lifecycle, and fixture factories.

Marker policy
-------------
Three markers are defined in ``pyproject.toml``: ``unit``, ``integration``, ``live``. Any test
that is not explicitly marked ``integration`` or ``live`` is automatically marked ``unit`` so the
default ``pytest -m unit`` run (``just test``) picks up the whole fast suite without per-test
decorators.

Database lifecycle
------------------
Integration tests depend on the ``db_engine`` / ``db_connection`` fixtures, which require
``TEST_DATABASE_URL``. When it is absent the fixtures *skip* with an actionable message instead of
failing, so the unit suite runs with no Docker and no credentials.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parent

# Ensure tests/ is importable for the shared factories regardless of plugin load order
# (mirrors `pythonpath = ["tests"]` in pyproject.toml).
_TESTS_DIR = str(_REPO_ROOT / "tests")
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

# Re-export the factories so tests can request them as fixtures (below) or import directly.
import factories  # noqa: E402  (import must follow the sys.path bootstrap above)

_SKIP_REASON = (
    "TEST_DATABASE_URL is not set. Start Postgres with `docker compose up -d` and set "
    "TEST_DATABASE_URL (see .env.example) to run integration tests."
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Default every otherwise-unmarked test to the ``unit`` marker."""
    for item in items:
        if "integration" not in item.keywords and "live" not in item.keywords:
            item.add_marker(pytest.mark.unit)


# --------------------------------------------------------------------------------------------
# Database lifecycle
# --------------------------------------------------------------------------------------------
def _test_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def test_database_url() -> str:
    """The configured test database URL, or skip the test if it is unavailable."""
    url = _test_database_url()
    if not url:
        pytest.skip(_SKIP_REASON)
    return url


@pytest.fixture
async def db_engine(test_database_url: str) -> AsyncIterator[Any]:
    """A disposable async engine bound to the test database.

    Imported lazily so the unit suite never imports SQLAlchemy's async stack unnecessarily.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(test_database_url, future=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_connection(db_engine: Any) -> AsyncIterator[Any]:
    """A connection wrapped in a transaction that is always rolled back.

    This keeps integration tests isolated: nothing they write survives the test.
    """
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            await transaction.rollback()


# --------------------------------------------------------------------------------------------
# Phase 1 row-writing harness (tasks 6 / 10–13)
# --------------------------------------------------------------------------------------------
# These two fixtures give Phase 1 repository integration tests a *migrated* schema and a
# session-per-test bound to a rolled-back transaction. They are written to slot in cleanly once
# the Alembic config (task 10) and the SQLAlchemy models / ``db.py`` (task 6) land.
#
# WIRE-IN CHECKLIST (do not forget when those tasks land):
#   - task 10: create the Alembic env at ``packages/storage/alembic.ini`` (+ ``migrations/``) with
#     the initial migration. ``db_schema`` will then stop skipping and run ``alembic upgrade head``
#     against TEST_DATABASE_URL automatically — no change needed here.
#   - task 6: nothing required for ``db_session`` (it binds a plain ``AsyncSession`` to the test
#     connection). If a ``Uow`` wrapper is preferred, add a sibling ``uow`` fixture that wraps this
#     same connection so repository code and the test share one rolled-back transaction.

# Location of the Alembic config, per AGENTS.md ("All migrations live in packages/storage ...").
_ALEMBIC_DIR = _REPO_ROOT / "packages" / "storage"
_ALEMBIC_INI = _ALEMBIC_DIR / "alembic.ini"


@pytest.fixture(scope="session")
def db_schema() -> str:
    """Run ``alembic upgrade head`` against TEST_DATABASE_URL once per session.

    Exercises the *real* migration path (preferred over ``Base.metadata.create_all``) so the schema
    Phase 1 tests write into is exactly what production gets. Skips gracefully when the test DB is
    unavailable, or — until task 10 lands — when the Alembic config does not exist yet.
    """
    import subprocess

    url = _test_database_url()
    if not url:
        pytest.skip(_SKIP_REASON)
    if not _ALEMBIC_INI.exists():
        pytest.skip(
            "Alembic config not found at packages/storage/alembic.ini. Migrations land in task 10; "
            "db_schema will run `alembic upgrade head` automatically once they exist."
        )

    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=_ALEMBIC_DIR,
        env={**os.environ, "TEST_DATABASE_URL": url, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")
    return url


@pytest.fixture
async def db_session(db_engine: Any, db_schema: str) -> AsyncIterator[Any]:
    """An ``AsyncSession`` bound to a connection-level transaction that is always rolled back.

    Same isolation pattern as ``db_connection``: the session and any repository code that uses it
    share one transaction, and nothing they write survives the test. Depends on ``db_schema`` so the
    tables exist (both skip together when TEST_DATABASE_URL / migrations are absent).
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


# --------------------------------------------------------------------------------------------
# Factory fixtures
# --------------------------------------------------------------------------------------------
@pytest.fixture
def tenant_id() -> Any:
    return factories.make_tenant_id()


@pytest.fixture
def make_access_descriptor() -> Callable[..., Any]:
    return factories.make_access_descriptor


@pytest.fixture
def make_source_snapshot() -> Callable[..., Any]:
    return factories.make_source_snapshot


@pytest.fixture
def make_normalized_document() -> Callable[..., Any]:
    return factories.make_normalized_document


@pytest.fixture
def make_chunk() -> Callable[..., Any]:
    return factories.make_chunk


@pytest.fixture
def make_extraction_envelope() -> Callable[..., Any]:
    return factories.make_extraction_envelope


@pytest.fixture
def make_job() -> Callable[..., Any]:
    return factories.make_job


@pytest.fixture
def make_new_job() -> Callable[..., Any]:
    return factories.make_new_job


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Prevent a developer's real shell environment from leaking into settings unit tests.

    Cognitio settings variables are cleared by default; tests that want a value set it explicitly
    via ``monkeypatch.setenv``. ``TEST_DATABASE_URL`` is preserved because the integration
    fixtures key off it.
    """
    preserved = {"TEST_DATABASE_URL"}
    for key in list(os.environ):
        if key in preserved:
            continue
        if key.startswith(
            (
                "DATABASE_URL",
                "NOTION_",
                "ANTHROPIC_",
                "EMBEDDING_",
                "OPENAI_",
                "FALLBACK_ACL_",
                "WORKER_",
            )
        ):
            monkeypatch.delenv(key, raising=False)
    yield
