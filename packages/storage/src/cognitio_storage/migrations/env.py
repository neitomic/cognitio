"""Async-compatible Alembic environment.

Reads the database URL from ``DATABASE_URL`` (falling back to ``TEST_DATABASE_URL``) so the
same migrations run against dev, the disposable test DB, and CI. Online migrations run through
an :class:`~sqlalchemy.ext.asyncio.AsyncEngine`.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Import the models so every table is registered on the shared metadata.
from cognitio_storage import models
from cognitio_storage.types import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
# Touch the models module so linters keep the import that populates the metadata.
assert models.Base is Base


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "Set DATABASE_URL (or TEST_DATABASE_URL) before running Alembic migrations."
        )
    return url


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live connection (``alembic upgrade --sql``)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: object) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)  # type: ignore[arg-type]
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine: AsyncEngine = create_async_engine(_database_url(), future=True)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
