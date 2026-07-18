"""Guarded project corpus and immutable index lifecycle routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.core.http.envelopes import ApiResponse
from app.dependencies.retrieval import IndexLifecycleServiceDep
from app.modules.retrieval.schemas.index_lifecycle import (
    IndexBuildListResponse,
    IndexBuildResponse,
    LifecycleJobResponse,
)

router = APIRouter()


@router.get("", response_model=ApiResponse[IndexBuildListResponse])
async def list_index_builds(
    project_id: uuid.UUID, service: IndexLifecycleServiceDep
) -> ApiResponse[IndexBuildListResponse]:
    del project_id
    builds, active_id, previous_id = await service.list()
    return ApiResponse.ok(
        IndexBuildListResponse(
            items=[IndexBuildResponse.model_validate(item) for item in builds],
            active_build_id=active_id,
            previous_build_id=previous_id,
        )
    )


@router.post(
    "/reembed",
    response_model=ApiResponse[LifecycleJobResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def reembed_corpus(
    project_id: uuid.UUID, service: IndexLifecycleServiceDep
) -> ApiResponse[LifecycleJobResponse]:
    del project_id
    return ApiResponse.ok(await service.enqueue_reembed())


@router.post(
    "/reindex",
    response_model=ApiResponse[LifecycleJobResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def reindex_corpus(
    project_id: uuid.UUID, service: IndexLifecycleServiceDep
) -> ApiResponse[LifecycleJobResponse]:
    del project_id
    return ApiResponse.ok(await service.enqueue_reindex())


@router.post(
    "/reconcile-storage",
    response_model=ApiResponse[LifecycleJobResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def reconcile_storage(
    project_id: uuid.UUID, service: IndexLifecycleServiceDep
) -> ApiResponse[LifecycleJobResponse]:
    del project_id
    return ApiResponse.ok(await service.enqueue_storage_reconciliation())


@router.post("/{build_id}/activate", response_model=ApiResponse[IndexBuildResponse])
async def activate_index_build(
    project_id: uuid.UUID,
    build_id: uuid.UUID,
    service: IndexLifecycleServiceDep,
) -> ApiResponse[IndexBuildResponse]:
    del project_id
    return ApiResponse.ok(IndexBuildResponse.model_validate(await service.activate(build_id)))


@router.post("/rollback", response_model=ApiResponse[IndexBuildResponse])
async def rollback_index_build(
    project_id: uuid.UUID, service: IndexLifecycleServiceDep
) -> ApiResponse[IndexBuildResponse]:
    del project_id
    return ApiResponse.ok(IndexBuildResponse.model_validate(await service.rollback()))
