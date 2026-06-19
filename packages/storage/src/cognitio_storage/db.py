"""Async engine, session factory, and the :class:`Uow` transactional primitive.

The Pipeline Layer (and any other caller that needs atomic multi-row commits) opens a
``Uow``: a unit-of-work that yields an :class:`~sqlalchemy.ext.asyncio.AsyncSession` and
**commits on a clean exit, rolls back on any exception**. Repositories take the session and
never own transaction boundaries themselves.
"""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

SessionFactory = async_sessionmaker[AsyncSession]


def create_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    """Create an async engine for the given asyncpg URL.

    ``pool_pre_ping`` guards against connections silently dropped by Postgres/proxies.
    """
    return create_async_engine(url, echo=echo, pool_pre_ping=True, future=True)


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    """A session factory bound to ``engine``; sessions keep attributes after commit."""
    return async_sessionmaker(engine, expire_on_commit=False)


class Uow:
    """Async unit of work: one transaction per ``async with`` block.

    ``async with Uow(session_factory) as session:`` yields a fresh session. On a clean exit
    the transaction is committed; if the body raises, it is rolled back. Either way the
    session is closed. Reusable: each ``async with`` opens an independent session.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> AsyncSession:
        self._session = self._session_factory()
        return self._session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        session = self._session
        if session is None:  # pragma: no cover - defensive; __aenter__ always runs first
            return
        try:
            if exc_type is None:
                await session.commit()
            else:
                await session.rollback()
        finally:
            await session.close()
            self._session = None
