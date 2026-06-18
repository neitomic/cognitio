"""Run validated extraction and persist records, mentions, and one cost event."""

from cognitio_pipeline.types import Job, JobType, NewJob
from cognitio_pipeline.worker import Transaction


class ExtractHandler:
    type = JobType.EXTRACT

    async def run(self, job: Job, tx: Transaction) -> tuple[NewJob, ...]:
        # TODO(Phase 1): call Extractor and commit all validated output atomically.
        raise NotImplementedError
