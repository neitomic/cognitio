"""Source and extraction provenance drilldown routes."""

from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID

from cognitio_query.types import Principal
from fastapi import APIRouter, Depends, HTTPException, Request, status

from cognitio_api.dependencies import app_service, get_principal
from cognitio_api.models import SourceItemDetail
from cognitio_api.services import SourceService

router = APIRouter(tags=["sources"])


def service(request: Request) -> SourceService:
    return cast(SourceService, app_service(request, "source_service"))


@router.get("/sources/{source_id}")
async def source(
    source_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    sources: Annotated[SourceService, Depends(service)],
) -> SourceItemDetail:
    item = await sources.get(principal, source_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    return item


@router.get("/sources/{source_id}/versions")
async def versions(
    source_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    sources: Annotated[SourceService, Depends(service)],
) -> tuple[dict[str, object], ...]:
    return await sources.versions(principal, source_id)


@router.get("/extractions/{extraction_id}")
async def extraction(
    extraction_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    sources: Annotated[SourceService, Depends(service)],
) -> dict[str, object]:
    item = await sources.extraction(principal, extraction_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Extraction not found")
    return item
