"""Phase 1 Notion connector skeleton.

The API-specific pagination and block rendering remain implementation work; this class fixes the
capability declaration and connector boundary used by the pipeline.
"""

from __future__ import annotations

from hashlib import sha256

from cognitio_connectors.base import (
    ChangeEvent,
    ConnectorCapabilities,
    Page,
    SourceRef,
    SourceSnapshot,
    Tombstone,
)
from cognitio_connectors.notion.client import NotionApi


class NotionConnector:
    name = "notion"

    def __init__(self, api: NotionApi, *, root_page_ids: frozenset[str] = frozenset()) -> None:
        self._api = api
        self._root_page_ids = root_page_ids

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            incremental_cursor=False,
            updated_since_filter=False,
            webhooks=False,
            tombstones=False,
            permission_metadata=False,
            child_expansion=True,
            stable_content_hash=True,
        )

    async def full_scan(self, cursor: str | None) -> Page[SourceRef]:
        # TODO(Phase 1): map Notion search results and scope them to configured roots.
        raise NotImplementedError

    async def incremental_scan(self, cursor: str | None) -> Page[ChangeEvent]:
        # TODO(Phase 1): reconciliation-window scan using last_edited_time.
        raise NotImplementedError

    async def fetch(self, ref: SourceRef) -> SourceSnapshot:
        # TODO(Phase 1): render the complete block tree, capture metadata, and derive revision.
        raise NotImplementedError

    async def fetch_children(self, ref: SourceRef, cursor: str | None) -> Page[SourceRef]:
        # TODO(Phase 1): map block children while preserving Notion pagination.
        raise NotImplementedError

    async def tombstone_scan(self, cursor: str | None) -> Page[Tombstone]:
        # TODO(Phase 1): diff scoped full-scan refs against the prior reconciliation set.
        raise NotImplementedError

    @staticmethod
    def content_hash(raw_content: bytes) -> bytes:
        return sha256(raw_content).digest()
