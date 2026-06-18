"""Public connector protocol and transport-independent sync value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from cognitio_storage.enums import ChangeType


@dataclass(frozen=True)
class ConnectorCapabilities:
    incremental_cursor: bool
    updated_since_filter: bool
    webhooks: bool
    tombstones: bool
    permission_metadata: bool
    child_expansion: bool
    stable_content_hash: bool


@dataclass(frozen=True)
class AccessDescriptor:
    """Captured source ACL. Query resolves group membership before enforcement."""

    public: bool = False
    allowed_principals: frozenset[str] = field(default_factory=frozenset)
    allowed_groups: frozenset[str] = field(default_factory=frozenset)
    denied_principals: frozenset[str] = field(default_factory=frozenset)
    denied_groups: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Page[T]:
    items: tuple[T, ...]
    next_cursor: str | None
    high_watermark: str | None
    sync_started_at: datetime
    has_more: bool
    retry_after: float | None = None


@dataclass(frozen=True)
class SourceRef:
    source_id: str
    object_type: str
    source_url: str | None = None
    parent_id: str | None = None


@dataclass(frozen=True)
class ChangeEvent:
    source_id: str
    change_type: ChangeType
    source_revision: int
    cursor: str | None
    occurred_at: datetime
    ref: SourceRef


@dataclass(frozen=True)
class Tombstone:
    source_id: str
    discovered_at: datetime
    source_revision: int


@dataclass(frozen=True)
class SourceSnapshot:
    source_id: str
    raw_content: bytes
    content_hash: bytes
    acl: AccessDescriptor
    source_timestamp: datetime | None
    source_revision: int
    metadata: dict[str, object]


@dataclass(frozen=True)
class ConnectorHealth:
    connector: str
    last_successful_reconciliation: datetime | None
    high_watermark: str | None
    cursor_lag_seconds: float | None
    dead_letter_count: int
    error: str | None = None


class Connector(Protocol):
    name: str

    def capabilities(self) -> ConnectorCapabilities: ...

    async def full_scan(self, cursor: str | None) -> Page[SourceRef]: ...

    async def incremental_scan(self, cursor: str | None) -> Page[ChangeEvent]: ...

    async def fetch(self, ref: SourceRef) -> SourceSnapshot: ...

    async def fetch_children(self, ref: SourceRef, cursor: str | None) -> Page[SourceRef]: ...

    async def tombstone_scan(self, cursor: str | None) -> Page[Tombstone]: ...
