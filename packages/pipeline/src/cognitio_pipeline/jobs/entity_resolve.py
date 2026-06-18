"""Resolve response-scoped mentions to canonical entities."""

from cognitio_pipeline.types import Job, JobType, NewJob
from cognitio_pipeline.worker import Transaction


class EntityResolveHandler:
    type = JobType.ENTITY_RESOLVE

    async def run(self, job: Job, tx: Transaction) -> tuple[NewJob, ...]:
        # TODO(Phase 1): exact alias blocking; TODO(Phase 2): hardened model-assisted resolution.
        raise NotImplementedError
