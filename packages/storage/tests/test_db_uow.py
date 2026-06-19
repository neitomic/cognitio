"""Unit tests for the :class:`Uow` transactional primitive (task 6).

These prove the commit/rollback contract without a database: a fake session records the
lifecycle calls the ``Uow`` makes on a clean exit versus an error.
"""

from __future__ import annotations

import pytest
from cognitio_storage.db import Uow


class _FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.closed = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def close(self) -> None:
        self.closed = True


class _FakeFactory:
    """Stand-in for ``async_sessionmaker``: hands out (and remembers) fake sessions."""

    def __init__(self) -> None:
        self.sessions: list[_FakeSession] = []

    def __call__(self) -> _FakeSession:
        session = _FakeSession()
        self.sessions.append(session)
        return session


async def test_uow_commits_on_success() -> None:
    factory = _FakeFactory()
    async with Uow(factory) as session:  # type: ignore[arg-type]
        assert session is factory.sessions[0]

    (committed,) = factory.sessions
    assert committed.committed is True
    assert committed.rolled_back is False
    assert committed.closed is True


async def test_uow_rolls_back_on_error() -> None:
    factory = _FakeFactory()

    with pytest.raises(ValueError, match="boom"):
        async with Uow(factory):  # type: ignore[arg-type]
            raise ValueError("boom")

    (session,) = factory.sessions
    assert session.committed is False
    assert session.rolled_back is True
    assert session.closed is True


async def test_uow_is_reusable_with_independent_sessions() -> None:
    factory = _FakeFactory()

    async with Uow(factory):  # type: ignore[arg-type]
        pass
    async with Uow(factory):  # type: ignore[arg-type]
        pass

    assert len(factory.sessions) == 2
    assert all(s.committed and s.closed for s in factory.sessions)
