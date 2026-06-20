"""Unit tests for the Notion connector + httpx adapter (task 13).

All network traffic is served by an ``httpx.MockTransport`` — no live credentials, fully
deterministic. Covers scoped discovery, the stable-hash block-tree fetch, child expansion,
the last_edited_time pre-fetch gate, and the typed error mapping (429 / 401 / malformed).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import pytest
from cognitio_connectors.notion.client import (
    NotionAuthError,
    NotionError,
    NotionHttpApi,
    NotionRateLimited,
)
from cognitio_connectors.notion.connector import NotionConnector

LAST_EDITED = "2024-05-01T10:00:00.000Z"


def _page_obj(page_id: str, *, parent_page: str | None = None, title: str = "Title") -> dict:
    parent: dict[str, Any] = (
        {"type": "page_id", "page_id": parent_page}
        if parent_page
        else {"type": "workspace", "workspace": True}
    )
    return {
        "object": "page",
        "id": page_id,
        "url": f"https://notion.so/{page_id}",
        "last_edited_time": LAST_EDITED,
        "parent": parent,
        "properties": {
            "title": {
                "type": "title",
                "title": [{"plain_text": title}],
            }
        },
        "archived": False,
    }


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/search":
        return httpx.Response(
            200,
            json={
                "results": [_page_obj("root-1"), _page_obj("other-1")],
                "next_cursor": None,
                "has_more": False,
            },
        )
    if path == "/v1/pages/root-1":
        return httpx.Response(200, json=_page_obj("root-1"))
    if path == "/v1/blocks/root-1/children":
        return httpx.Response(
            200,
            json={
                "results": [
                    {"id": "b1", "type": "paragraph", "paragraph": {"x": 1}, "has_children": False},
                    {"id": "b2", "type": "toggle", "toggle": {}, "has_children": True},
                    {
                        "id": "sub-1",
                        "type": "child_page",
                        "child_page": {"title": "Sub"},
                        "has_children": True,
                    },
                ],
                "next_cursor": None,
                "has_more": False,
            },
        )
    if path == "/v1/blocks/b2/children":
        return httpx.Response(
            200,
            json={
                "results": [
                    {"id": "b3", "type": "paragraph", "paragraph": {"y": 2}, "has_children": False}
                ],
                "next_cursor": None,
                "has_more": False,
            },
        )
    if path == "/v1/blocks/sub-1/children":
        return httpx.Response(200, json={"results": [], "next_cursor": None, "has_more": False})
    return httpx.Response(404, json={"object": "error"})


def _connector(handler: Any = _handler, *, roots: frozenset[str] = frozenset()) -> NotionConnector:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://api.notion.com/v1")
    api = NotionHttpApi("tok", client=client)
    return NotionConnector(api, root_page_ids=roots, fallback_acl_principals=frozenset({"alice"}))


def test_connector_is_capability_honest() -> None:
    caps = _connector().capabilities()
    assert caps.permission_metadata is False
    assert caps.tombstones is False
    assert caps.child_expansion is True


async def test_full_scan_scopes_to_roots() -> None:
    connector = _connector(roots=frozenset({"root-1"}))
    page = await connector.full_scan(None)
    assert [r.source_id for r in page.items] == ["root-1"]
    assert page.high_watermark == LAST_EDITED


async def test_full_scan_unscoped_returns_all() -> None:
    connector = _connector()
    page = await connector.full_scan(None)
    assert {r.source_id for r in page.items} == {"root-1", "other-1"}


async def test_incremental_scan_emits_change_events() -> None:
    connector = _connector(roots=frozenset({"root-1"}))
    page = await connector.incremental_scan(None)
    assert len(page.items) == 1
    event = page.items[0]
    assert event.source_id == "root-1"
    assert event.source_revision == int(
        datetime.fromisoformat(LAST_EDITED.replace("Z", "+00:00")).timestamp()
    )


async def test_fetch_is_byte_stable() -> None:
    connector = _connector()
    ref = (await connector.full_scan(None)).items[0]
    snap1 = await connector.fetch(ref)
    snap2 = await connector.fetch(ref)
    # deterministic canonical serialisation -> identical hash across runs
    assert snap1.content_hash == snap2.content_hash
    assert snap1.raw_content == snap2.raw_content
    assert snap1.metadata["title"] == "Title"
    # nested toggle children were traversed into the snapshot
    assert b"b3" in snap1.raw_content
    # fallback ACL applied (no permission metadata)
    assert snap1.acl.allowed_principals == frozenset({"alice"})


async def test_fetch_children_returns_child_pages_only() -> None:
    connector = _connector()
    ref = (await connector.full_scan(None)).items[0]
    children = await connector.fetch_children(ref, None)
    assert [c.source_id for c in children.items] == ["sub-1"]
    assert children.items[0].parent_id == "root-1"


def test_prefetch_gate_skips_unchanged() -> None:
    older = datetime.fromisoformat("2024-05-01T10:00:00+00:00")
    newer = datetime.fromisoformat("2024-06-01T10:00:00+00:00")
    # never seen -> must fetch
    assert NotionConnector.needs_fetch(last_edited_time=newer, recorded_source_timestamp=None)
    # unchanged -> skip
    assert not NotionConnector.needs_fetch(last_edited_time=older, recorded_source_timestamp=older)
    # newer edit -> fetch
    assert NotionConnector.needs_fetch(last_edited_time=newer, recorded_source_timestamp=older)


# --- error mapping ---------------------------------------------------------------------------
async def test_rate_limit_maps_to_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "7"}, json={})

    connector = _connector(handler)
    with pytest.raises(NotionRateLimited) as exc:
        await connector.full_scan(None)
    assert exc.value.retry_after == pytest.approx(7.0)


async def test_auth_error_maps() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    with pytest.raises(NotionAuthError):
        await _connector(handler).full_scan(None)


async def test_malformed_response_maps() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    with pytest.raises(NotionError):
        await _connector(handler).full_scan(None)
