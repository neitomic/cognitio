"""Integration tests that require a live Postgres (skip cleanly without TEST_DATABASE_URL).

These double as the acceptance check for the compose stack: both the dev and test databases must
expose the ``vector`` and ``pgcrypto`` extensions.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def test_can_connect_to_test_database(db_connection) -> None:  # type: ignore[no-untyped-def]
    result = await db_connection.execute(text("SELECT 1"))
    assert result.scalar_one() == 1


async def test_required_extensions_are_present(db_connection) -> None:  # type: ignore[no-untyped-def]
    result = await db_connection.execute(
        text("SELECT extname FROM pg_extension WHERE extname IN ('vector', 'pgcrypto')")
    )
    extensions = {row[0] for row in result}
    assert {"vector", "pgcrypto"} <= extensions, f"missing required extensions; found {extensions}"
