"""Authentication and service lookup dependencies."""

from __future__ import annotations

from uuid import UUID

from cognitio_query.types import Principal
from fastapi import HTTPException, Request, status


def get_principal(request: Request) -> Principal:
    """Phase 1 header auth boundary; replace with verified identity-provider claims."""
    tenant = request.headers.get("x-cognitio-tenant-id")
    principal = request.headers.get("x-cognitio-principal-id")
    if tenant is None or principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Cognitio principal headers",
        )
    try:
        return Principal(id=UUID(principal), tenant_id=UUID(tenant))
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Cognitio principal headers",
        ) from error


def app_service(request: Request, name: str) -> object:
    service = getattr(request.app.state, name, None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Service {name!r} is not configured",
        )
    return service
