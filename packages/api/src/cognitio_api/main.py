"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from cognitio_api.routes import review, search, sources, sync


def create_app() -> FastAPI:
    application = FastAPI(title="Cognitio API", version="0.1.0")
    application.include_router(sync.router)
    application.include_router(review.router)
    application.include_router(search.router)
    application.include_router(sources.router)

    @application.get("/healthz", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
