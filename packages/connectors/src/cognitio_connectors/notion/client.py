"""Small typed boundary around the Notion HTTP API."""

from __future__ import annotations

from typing import Protocol


class NotionApi(Protocol):
    async def search(self, *, start_cursor: str | None) -> dict[str, object]: ...

    async def retrieve_page(self, page_id: str) -> dict[str, object]: ...

    async def retrieve_block(self, block_id: str) -> dict[str, object]: ...

    async def list_block_children(
        self,
        block_id: str,
        *,
        start_cursor: str | None,
    ) -> dict[str, object]: ...
