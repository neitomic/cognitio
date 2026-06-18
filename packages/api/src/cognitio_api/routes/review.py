"""Human review queue and decisions."""

from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID

from cognitio_query.types import Principal
from cognitio_review.queue import ReviewService
from cognitio_review.types import ReviewDecision, ReviewFilters, ReviewItem, ReviewPage
from cognitio_storage.enums import NodeType, Workflow
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from cognitio_api.dependencies import app_service, get_principal
from cognitio_api.models import ReviewEditRequest, ReviewItemDetail
from cognitio_api.services import ReviewDetailService

router = APIRouter(prefix="/review", tags=["review"])


def review_service(request: Request) -> ReviewService:
    return cast(ReviewService, app_service(request, "review_service"))


def detail_service(request: Request) -> ReviewDetailService:
    return cast(ReviewDetailService, app_service(request, "review_detail_service"))


@router.get("")
async def queue(
    principal: Annotated[Principal, Depends(get_principal)],
    reviews: Annotated[ReviewService, Depends(review_service)],
    workflow: Workflow | None = None,
    node_type: NodeType | None = None,
    source_item_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> ReviewPage:
    filters = ReviewFilters(
        workflow=workflow,
        node_type=node_type,
        source_item_id=source_item_id,
        limit=limit,
        cursor=cursor,
    )
    return await reviews.queue(principal.tenant_id, filters)


@router.get("/{item_id}")
async def detail(
    item_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    details: Annotated[ReviewDetailService, Depends(detail_service)],
) -> ReviewItemDetail:
    item = await details.get(principal, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review item not found")
    return item


async def _decide(
    item_id: UUID,
    decision: ReviewDecision,
    principal: Principal,
    reviews: ReviewService,
    edit: dict[str, object] | None = None,
) -> ReviewItem:
    return await reviews.decide(
        principal.tenant_id,
        item_id,
        decision,
        principal.id,
        edit,
    )


@router.post("/{item_id}/confirm")
async def confirm(
    item_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    reviews: Annotated[ReviewService, Depends(review_service)],
) -> ReviewItem:
    return await _decide(item_id, ReviewDecision.CONFIRM, principal, reviews)


@router.post("/{item_id}/edit")
async def edit(
    item_id: UUID,
    body: ReviewEditRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    reviews: Annotated[ReviewService, Depends(review_service)],
) -> ReviewItem:
    return await _decide(item_id, ReviewDecision.EDIT, principal, reviews, body.payload)


@router.post("/{item_id}/reject")
async def reject(
    item_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    reviews: Annotated[ReviewService, Depends(review_service)],
) -> ReviewItem:
    return await _decide(item_id, ReviewDecision.REJECT, principal, reviews)
