"""Fetch a source snapshot and enqueue normalization for new content."""

from cognitio_pipeline.types import Job, JobType, NewJob
from cognitio_pipeline.worker import Transaction


class FetchHandler:
    type = JobType.FETCH

    async def run(self, job: Job, tx: Transaction) -> tuple[NewJob, ...]:
        # TODO(Phase 1): resolve connector, enforce monotonic revision, write immutable snapshot.
        raise NotImplementedError
