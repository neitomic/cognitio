"""Compute stable chunk boundaries and enqueue work only for changed hashes."""

from cognitio_pipeline.types import Job, JobType, NewJob
from cognitio_pipeline.worker import Transaction


class ChunkHandler:
    type = JobType.CHUNK

    async def run(self, job: Job, tx: Transaction) -> tuple[NewJob, ...]:
        # TODO(Phase 1): diff per-chunk hashes and enqueue invalidate/embed/extract atomically.
        raise NotImplementedError
