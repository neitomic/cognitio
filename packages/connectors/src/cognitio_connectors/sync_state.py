"""Sync cursor persistence contract and connector retry policy."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class SyncCursorStore(Protocol):
    async def load(self, tenant_id: UUID, connector: str) -> str | None: ...

    async def checkpoint(
        self,
        tenant_id: UUID,
        connector: str,
        cursor: str | None,
        high_watermark: str | None,
    ) -> None: ...


@dataclass(frozen=True)
class RetryPolicy:
    base_seconds: float = 2.0
    max_seconds: float = 300.0
    max_attempts: int = 5
    jitter_ratio: float = 0.2

    def next_delay(self, attempts: int) -> float:
        exponent = max(attempts - 1, 0)
        delay = min(self.max_seconds, self.base_seconds * (2**exponent))
        jitter = delay * self.jitter_ratio
        return float(max(0.0, delay + random.uniform(-jitter, jitter)))

    def is_dead_letter(self, attempts: int) -> bool:
        return attempts >= self.max_attempts
