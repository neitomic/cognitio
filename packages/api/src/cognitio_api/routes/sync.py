"""Connector health and reconciliation routes."""

from __future__ import annotations

from typing import Annotated, cast

from cognitio_connectors.base import ConnectorHealth
from cognitio_query.types import Principal
from fastapi import APIRouter, Depends, HTTPException, Request, status

from cognitio_api.dependencies import app_service, get_principal
from cognitio_api.models import ReconcileResponse
from cognitio_api.services import SyncService

router = APIRouter(prefix="/connectors", tags=["sync"])


def service(request: Request) -> SyncService:
    return cast(SyncService, app_service(request, "sync_service"))


@router.get("")
async def connectors(
    principal: Annotated[Principal, Depends(get_principal)],
    sync: Annotated[SyncService, Depends(service)],
) -> tuple[ConnectorHealth, ...]:
    return await sync.list_health(principal.tenant_id)


@router.get("/{connector}/status")
async def connector_status(
    connector: str,
    principal: Annotated[Principal, Depends(get_principal)],
    sync: Annotated[SyncService, Depends(service)],
) -> ConnectorHealth:
    health = await sync.health(principal.tenant_id, connector)
    if health is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector not found")
    return health


@router.post("/{connector}/reconcile", status_code=status.HTTP_202_ACCEPTED)
async def reconcile(
    connector: str,
    principal: Annotated[Principal, Depends(get_principal)],
    sync: Annotated[SyncService, Depends(service)],
) -> ReconcileResponse:
    return ReconcileResponse(job_id=await sync.reconcile(principal.tenant_id, connector))
