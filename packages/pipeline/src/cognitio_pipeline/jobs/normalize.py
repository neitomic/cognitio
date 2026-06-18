"""Normalize immutable source bytes into stable text."""

from cognitio_pipeline.types import Job, JobType, NewJob
from cognitio_pipeline.worker import Transaction


class NormalizeHandler:
    type = JobType.NORMALIZE

    async def run(self, job: Job, tx: Transaction) -> tuple[NewJob, ...]:
        # TODO(Phase 1): normalize without destroying operative wording or offset stability.
        raise NotImplementedError
