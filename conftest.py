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

# Ensure tests/ is importable for the shared factories regardless of plugin load order
# (mirrors `pythonpath = ["tests"]` in pyproject.toml).
_TESTS_DIR = str(Path(__file__).resolve().parent / "tests")
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
