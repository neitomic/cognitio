"""ACL-filtered semantic search route."""

from __future__ import annotations

from typing import Annotated, cast

from cognitio_query.search import SearchService
from cognitio_query.types import Principal, SearchHit
from fastapi import APIRouter, Depends, Query, Request

from cognitio_api.dependencies import app_service, get_principal

router = APIRouter(tags=["search"])


def service(request: Request) -> SearchService:
    return cast(SearchService, app_service(request, "search_service"))


@router.get("/search")
async def search(
    principal: Annotated[Principal, Depends(get_principal)],
    searches: Annotated[SearchService, Depends(service)],
    q: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    sim_floor: Annotated[float, Query(ge=0.0, le=1.0)] = 0.75,
    model_version: Annotated[str, Query(min_length=1)] = "text-embedding-3-small:v1",
) -> tuple[SearchHit, ...]:
    return await searches.search(
        principal,
        q,
        model_version=model_version,
        similarity_floor=sim_floor,
        limit=limit,
    )
