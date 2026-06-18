"""Create a model-version-pinned vector for one object or chunk."""

from cognitio_pipeline.types import Job, JobType, NewJob
from cognitio_pipeline.worker import Transaction


class EmbedHandler:
    type = JobType.EMBED

    async def run(self, job: Job, tx: Transaction) -> tuple[NewJob, ...]:
        # TODO(Phase 1): call the configured embedder and idempotently persist its vector.
        raise NotImplementedError
