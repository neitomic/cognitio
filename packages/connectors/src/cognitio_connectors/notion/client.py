"""Typed boundary around the Notion HTTP API + a concrete ``httpx`` adapter.

``NotionApi`` is the protocol the connector codes against; ``NotionHttpApi`` is the real
implementation. Errors are mapped to a small typed hierarchy so the connector / reconciler can
distinguish a rate-limit (retry with ``retry_after``) from auth failure or a malformed payload.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

NOTION_BASE_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionError(Exception):
    """Base class for Notion adapter errors."""


class NotionAuthError(NotionError):
    """401/403 — token missing, invalid, or lacking access."""


class NotionNotFound(NotionError):
    """404 — the object does not exist or is not shared with the integration."""


class NotionRateLimited(NotionError):
    """429 — includes the ``Retry-After`` hint (seconds) when the API supplies one."""

    def __init__(self, retry_after: float | None) -> None:
        self.retry_after = retry_after
        super().__init__(f"Notion rate limited (retry_after={retry_after})")


class NotionServerError(NotionError):
    """5xx — transient server-side failure; the caller may retry with backoff."""


class NotionApi(Protocol):
    async def search(self, *, start_cursor: str | None) -> dict[str, Any]: ...

    async def retrieve_page(self, page_id: str) -> dict[str, Any]: ...

    async def retrieve_block(self, block_id: str) -> dict[str, Any]: ...

    async def list_block_children(
        self, block_id: str, *, start_cursor: str | None
    ) -> dict[str, Any]: ...


class NotionHttpApi:
    """Concrete ``httpx``-backed :class:`NotionApi`.

    A pre-built ``httpx.AsyncClient`` may be injected (tests pass one wired to a
    ``MockTransport``); otherwise one is created with the auth + version headers.
    """

    def __init__(
        self,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = NOTION_BASE_URL,
        version: str = NOTION_VERSION,
        timeout: float = 30.0,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": version,
                "Content-Type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(self, *, start_cursor: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "filter": {"property": "object", "value": "page"},
            "sort": {"direction": "descending", "timestamp": "last_edited_time"},
        }
        if start_cursor is not None:
            body["start_cursor"] = start_cursor
        response = await self._client.post("/search", json=body)
        return self._unwrap(response)

    async def retrieve_page(self, page_id: str) -> dict[str, Any]:
        response = await self._client.get(f"/pages/{page_id}")
        return self._unwrap(response)

    async def retrieve_block(self, block_id: str) -> dict[str, Any]:
        response = await self._client.get(f"/blocks/{block_id}")
        return self._unwrap(response)

    async def list_block_children(
        self, block_id: str, *, start_cursor: str | None
    ) -> dict[str, Any]:
        params = {"start_cursor": start_cursor} if start_cursor is not None else None
        response = await self._client.get(f"/blocks/{block_id}/children", params=params)
        return self._unwrap(response)

    @staticmethod
    def _unwrap(response: httpx.Response) -> dict[str, Any]:
        status = response.status_code
        if status == 429:
            raise NotionRateLimited(_retry_after(response))
        if status in (401, 403):
            raise NotionAuthError(f"Notion auth failed ({status})")
        if status == 404:
            raise NotionNotFound("Notion object not found (404)")
        if status >= 500:
            raise NotionServerError(f"Notion server error ({status})")
        if status >= 400:
            raise NotionError(f"Notion request failed ({status})")
        try:
            payload = response.json()
        except ValueError as exc:  # malformed / non-JSON body
            raise NotionError("Notion returned a malformed (non-JSON) response") from exc
        if not isinstance(payload, dict):
            raise NotionError("Notion returned an unexpected (non-object) response")
        return payload


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
