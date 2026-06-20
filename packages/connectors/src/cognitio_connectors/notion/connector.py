"""Phase 1 Notion connector.

Implements the capability-aware sync contract over the Notion HTTP API: scoped page
discovery (``full_scan`` / ``incremental_scan``), block-tree fetch with a canonical,
stable-hash snapshot (``fetch``), child expansion (``fetch_children``), and the cheap
**last_edited_time pre-fetch gate** (skip a block-tree fetch entirely when a page's
``last_edited_time`` is unchanged versus the recorded sync state).

Tombstone discovery is not a Notion feature — deletions are found by reconciliation diffing
full-scan membership (task 18), so ``tombstone_scan`` yields nothing and ``capabilities``
reports ``tombstones=False``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from cognitio_storage.enums import ChangeType

from cognitio_connectors.base import (
    AbstractConnector,
    AccessDescriptor,
    ChangeEvent,
    ConnectorCapabilities,
    Page,
    SourceRef,
    SourceSnapshot,
    Tombstone,
)
from cognitio_connectors.notion.client import NotionApi

# Bound recursion so a pathological/cyclic block tree cannot wedge a fetch.
_MAX_BLOCK_DEPTH = 20


class NotionConnector(AbstractConnector):
    name = "notion"

    def __init__(
        self,
        api: NotionApi,
        *,
        root_page_ids: frozenset[str] = frozenset(),
        fallback_acl_principals: frozenset[str] = frozenset(),
    ) -> None:
        self._api = api
        self._root_page_ids = root_page_ids
        self._fallback_acl_principals = fallback_acl_principals

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            incremental_cursor=False,
            updated_since_filter=True,
            webhooks=False,
            tombstones=False,
            permission_metadata=False,
            child_expansion=True,
            stable_content_hash=True,
        )

    # --- discovery ---------------------------------------------------------------------------
    async def full_scan(self, cursor: str | None) -> Page[SourceRef]:
        data = await self._api.search(start_cursor=cursor)
        results = _as_list(data.get("results"))
        refs = tuple(self._to_ref(obj) for obj in results if self._in_scope(obj))
        return Page(
            items=refs,
            next_cursor=_as_opt_str(data.get("next_cursor")),
            high_watermark=_max_last_edited(results),
            sync_started_at=datetime.now(UTC),
            has_more=bool(data.get("has_more", False)),
        )

    async def incremental_scan(self, cursor: str | None) -> Page[ChangeEvent]:
        data = await self._api.search(start_cursor=cursor)
        results = _as_list(data.get("results"))
        events: list[ChangeEvent] = []
        for obj in results:
            if not self._in_scope(obj):
                continue
            last_edited = _parse_dt(obj.get("last_edited_time"))
            events.append(
                ChangeEvent(
                    source_id=str(obj["id"]),
                    change_type=ChangeType.UPDATED,
                    source_revision=_revision(last_edited),
                    cursor=_as_opt_str(data.get("next_cursor")),
                    occurred_at=last_edited or datetime.now(UTC),
                    ref=self._to_ref(obj),
                )
            )
        return Page(
            items=tuple(events),
            next_cursor=_as_opt_str(data.get("next_cursor")),
            high_watermark=_max_last_edited(results),
            sync_started_at=datetime.now(UTC),
            has_more=bool(data.get("has_more", False)),
        )

    # --- pre-fetch gate ----------------------------------------------------------------------
    @staticmethod
    def needs_fetch(
        *, last_edited_time: datetime | None, recorded_source_timestamp: datetime | None
    ) -> bool:
        """Cheap gate: fetch the block tree only when ``last_edited_time`` advanced.

        Returns ``True`` (fetch) when we have never seen the page or its edit time is newer
        than the one recorded on the current source version; ``False`` (skip) when unchanged.
        """
        if last_edited_time is None:
            return True
        if recorded_source_timestamp is None:
            return True
        return last_edited_time > recorded_source_timestamp

    # --- fetch -------------------------------------------------------------------------------
    async def fetch(self, ref: SourceRef) -> SourceSnapshot:
        page = await self._api.retrieve_page(ref.source_id)
        blocks = await self._collect_blocks(ref.source_id, depth=0)
        # Canonical, sorted JSON -> byte-stable content hash across runs.
        canonical = {"page": _canonical_page(page), "blocks": blocks}
        raw_content = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        last_edited = _parse_dt(page.get("last_edited_time"))
        return SourceSnapshot(
            source_id=ref.source_id,
            raw_content=raw_content,
            content_hash=sha256(raw_content).digest(),
            acl=self._fallback_acl(),
            source_timestamp=last_edited,
            source_revision=_revision(last_edited),
            metadata={"title": _title(page), "url": _as_opt_str(page.get("url"))},
        )

    async def fetch_children(self, ref: SourceRef, cursor: str | None) -> Page[SourceRef]:
        data = await self._api.list_block_children(ref.source_id, start_cursor=cursor)
        results = _as_list(data.get("results"))
        child_refs = tuple(
            SourceRef(source_id=str(block["id"]), object_type="page", parent_id=ref.source_id)
            for block in results
            if block.get("type") == "child_page"
        )
        return Page(
            items=child_refs,
            next_cursor=_as_opt_str(data.get("next_cursor")),
            high_watermark=None,
            sync_started_at=datetime.now(UTC),
            has_more=bool(data.get("has_more", False)),
        )

    async def tombstone_scan(self, cursor: str | None) -> Page[Tombstone]:
        # Notion has no deletion feed; reconciliation (task 18) diffs full-scan membership.
        return Page(
            items=(),
            next_cursor=None,
            high_watermark=None,
            sync_started_at=datetime.now(UTC),
            has_more=False,
        )

    # --- internals ---------------------------------------------------------------------------
    async def _collect_blocks(self, block_id: str, *, depth: int) -> list[dict[str, Any]]:
        """Recursively gather a block's children, paginating each level."""
        if depth >= _MAX_BLOCK_DEPTH:
            return []
        collected: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            data = await self._api.list_block_children(block_id, start_cursor=cursor)
            for block in _as_list(data.get("results")):
                entry = _canonical_block(block)
                if block.get("has_children"):
                    entry["children"] = await self._collect_blocks(
                        str(block["id"]), depth=depth + 1
                    )
                collected.append(entry)
            if not data.get("has_more"):
                break
            cursor = _as_opt_str(data.get("next_cursor"))
        return collected

    def _in_scope(self, obj: dict[str, Any]) -> bool:
        if obj.get("object") != "page":
            return False
        if not self._root_page_ids:
            return True
        source_id = str(obj.get("id", ""))
        return source_id in self._root_page_ids or _parent_id(obj) in self._root_page_ids

    def _to_ref(self, obj: dict[str, Any]) -> SourceRef:
        return SourceRef(
            source_id=str(obj["id"]),
            object_type="page",
            source_url=_as_opt_str(obj.get("url")),
            parent_id=_parent_id(obj),
        )

    def _fallback_acl(self) -> AccessDescriptor:
        # permission_metadata=False: apply the configured fallback principals.
        return AccessDescriptor(public=False, allowed_principals=self._fallback_acl_principals)


# --- module-level coercion / canonicalisation helpers ----------------------------------------
def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _as_opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _revision(last_edited: datetime | None) -> int:
    """Monotonic-ish per-page revision derived from the source edit time (epoch seconds)."""
    if last_edited is None:
        return 0
    return int(last_edited.timestamp())


def _parent_id(obj: dict[str, Any]) -> str | None:
    parent = obj.get("parent")
    if not isinstance(parent, dict):
        return None
    for key in ("page_id", "database_id", "block_id"):
        value = parent.get(key)
        if isinstance(value, str):
            return value
    return None


def _title(page: dict[str, Any]) -> str | None:
    properties = page.get("properties")
    if not isinstance(properties, dict):
        return None
    for prop in properties.values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            parts = prop.get("title")
            if isinstance(parts, list):
                text = "".join(str(p.get("plain_text", "")) for p in parts if isinstance(p, dict))
                return text or None
    return None


def _canonical_page(page: dict[str, Any]) -> dict[str, Any]:
    """Stable subset of a page object used for hashing (drops volatile request metadata)."""
    return {
        "id": page.get("id"),
        "properties": page.get("properties"),
        "parent": page.get("parent"),
        "archived": page.get("archived", False),
    }


def _canonical_block(block: dict[str, Any]) -> dict[str, Any]:
    block_type = block.get("type")
    content = block.get(block_type) if isinstance(block_type, str) else None
    return {
        "id": block.get("id"),
        "type": block_type,
        "content": content,
        "has_children": bool(block.get("has_children", False)),
    }


def _max_last_edited(results: list[dict[str, Any]]) -> str | None:
    times: list[str] = []
    for obj in results:
        value = obj.get("last_edited_time")
        if isinstance(value, str):
            times.append(value)
    return max(times) if times else None
