"""Mark changed-chunk dependants stale and enqueue resumable re-derivation."""

from cognitio_pipeline.types import Job, JobType, NewJob
from cognitio_pipeline.worker import Transaction


class InvalidateHandler:
    type = JobType.INVALIDATE

    async def run(self, job: Job, tx: Transaction) -> tuple[NewJob, ...]:
        # TODO(Phase 1): mark each extraction stale and enqueue one deduplicated re-derive job.
        raise NotImplementedError
