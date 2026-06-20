"""Unit + integration tests for the connector base contract and sync state (task 12)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from cognitio_connectors.base import (
    ChangeEvent,
    Connector,
    ConnectorCapabilities,
    Page,
    SourceRef,
    SourceSnapshot,
    SyncCheckpoint,
    Tombstone,
)
from cognitio_connectors.sync_state import DbSyncCursorStore, RetryPolicy
from sqlalchemy.ext.asyncio import AsyncSession


class _FakeConnector:
    """A minimal mock connector implementing the structural ``Connector`` interface."""

    name = "fake"

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            incremental_cursor=True,
            updated_since_filter=True,
            webhooks=False,
            tombstones=True,
            permission_metadata=True,
            child_expansion=True,
            stable_content_hash=True,
        )

    async def full_scan(self, cursor: str | None) -> Page[SourceRef]:
        return Page((), None, "hwm", datetime.now(UTC), False)

    async def incremental_scan(self, cursor: str | None) -> Page[ChangeEvent]:
        return Page((), None, "hwm", datetime.now(UTC), False)

    async def fetch(self, ref: SourceRef) -> SourceSnapshot:
        raise NotImplementedError

    async def fetch_children(self, ref: SourceRef, cursor: str | None) -> Page[SourceRef]:
        return Page((), None, None, datetime.now(UTC), False)

    async def tombstone_scan(self, cursor: str | None) -> Page[Tombstone]:
        return Page((), None, None, datetime.now(UTC), False)


def test_mock_connector_satisfies_protocol() -> None:
    connector = _FakeConnector()
    assert isinstance(connector, Connector)
    assert connector.capabilities().incremental_cursor is True


async def test_mock_connector_methods_callable() -> None:
    connector = _FakeConnector()
    page = await connector.full_scan(None)
    assert page.high_watermark == "hwm"
    assert page.items == ()


def test_retry_policy_backoff_and_dead_letter() -> None:
    policy = RetryPolicy(base_seconds=2.0, max_seconds=60.0, max_attempts=5, jitter_ratio=0.0)
    assert policy.next_delay(1) == pytest.approx(2.0)
    assert policy.next_delay(2) == pytest.approx(4.0)
    assert policy.next_delay(3) == pytest.approx(8.0)
    # capped at max_seconds
    assert policy.next_delay(100) == pytest.approx(60.0)
    assert policy.is_dead_letter(4) is False
    assert policy.is_dead_letter(5) is True


def test_sync_checkpoint_value_object() -> None:
    cp = SyncCheckpoint(cursor="c1", high_watermark="hwm", scan_generation=3)
    assert (cp.cursor, cp.high_watermark, cp.scan_generation) == ("c1", "hwm", 3)


# --- integration: checkpoint persistence via connector_sync_states --------------------------
pytest_integration = pytest.mark.integration


@pytest_integration
async def test_db_sync_cursor_store_round_trip(db_session: AsyncSession) -> None:
    tenant = uuid.uuid4()
    store = DbSyncCursorStore(db_session)

    # nothing recorded yet
    assert await store.load(tenant, "notion") is None
    empty = await store.load_checkpoint(tenant, "notion")
    assert empty == SyncCheckpoint(cursor=None, high_watermark=None, scan_generation=0)

    await store.checkpoint(tenant, "notion", "cursor-1", "hwm-1")
    assert await store.load(tenant, "notion") == "cursor-1"
    cp = await store.load_checkpoint(tenant, "notion")
    assert cp.cursor == "cursor-1" and cp.high_watermark == "hwm-1"

    # an empty scan still advances the high-watermark
    await store.checkpoint(tenant, "notion", None, "hwm-2")
    cp = await store.load_checkpoint(tenant, "notion")
    assert cp.high_watermark == "hwm-2"
