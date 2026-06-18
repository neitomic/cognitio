"""Asynchronous worker loop and typed handler registry."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Protocol

from cognitio_pipeline.queue import JobQueue
from cognitio_pipeline.types import Job, JobType, NewJob

logger = logging.getLogger(__name__)


class Transaction(Protocol):
    """Marker protocol for the storage unit-of-work transaction passed to handlers."""


class TransactionFactory(Protocol):
    def __call__(self) -> Transaction: ...


class JobHandler(Protocol):
    type: JobType

    async def run(self, job: Job, tx: Transaction) -> tuple[NewJob, ...]: ...


class Worker:
    def __init__(
        self,
        queue: JobQueue,
        handlers: Mapping[JobType, JobHandler],
        transaction_factory: TransactionFactory,
        *,
        worker_id: str,
        idle_seconds: float = 1.0,
    ) -> None:
        self._queue = queue
        self._handlers = handlers
        self._transaction_factory = transaction_factory
        self._worker_id = worker_id
        self._idle_seconds = idle_seconds

    async def run_forever(self) -> None:
        while True:
            worked = await self.run_once()
            if not worked:
                await asyncio.sleep(self._idle_seconds)

    async def run_once(self) -> bool:
        job = await self._queue.claim(self._worker_id)
        if job is None:
            return False
        handler = self._handlers.get(job.type)
        if handler is None:
            await self._queue.fail(job, f"No handler registered for {job.type}")
            return True
        try:
            follow_ons = await handler.run(job, self._transaction_factory())
            await self._queue.complete(job, enqueue=follow_ons)
        except Exception as error:
            logger.exception("Job %s failed", job.id)
            await self._queue.fail(job, str(error))
        return True
