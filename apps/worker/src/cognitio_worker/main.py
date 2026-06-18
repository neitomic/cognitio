"""Worker composition root."""

from __future__ import annotations

import asyncio


async def run() -> None:
    # TODO(Phase 1): construct storage Uow/queue, Notion connector, extractor, and handlers.
    # Keeping wiring here prevents package layers from depending upward on the runnable app.
    raise NotImplementedError("Worker dependencies are not configured")


def main() -> None:
    asyncio.run(run())
