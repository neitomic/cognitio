"""Storage-owned job queue contract consumed by the worker."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from cognitio_pipeline.types import Job, JobPayload, JobType, NewJob


class JobQueue(Protocol):
    async def enqueue(
        self,
        tenant_id: UUID,
        type: JobType,
        payload: JobPayload,
        *,
        dedupe_key: str | None = None,
        priority: int = 100,
        run_after: datetime | None = None,
    ) -> UUID: ...

    async def claim(self, worker_id: str) -> Job | None:
        """Claim with `FOR UPDATE SKIP LOCKED` in the storage implementation."""
        ...

    async def complete(self, job: Job, *, enqueue: tuple[NewJob, ...]) -> None:
        """Atomically mark done and enqueue all follow-on jobs."""
        ...

    async def fail(self, job: Job, error: str) -> None: ...

    async def requeue_stuck(self, *, older_than: datetime) -> int: ...
