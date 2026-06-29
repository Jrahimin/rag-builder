"""Project management HTTP routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.dependencies.projects import ProjectServiceDep
from app.modules.projects.schemas.project import (
    ProjectCreate,
    ProjectResponse,
    ProjectStatusUpdate,
    ProjectUpdate,
)
from app.platform.http.envelopes import ApiResponse
from app.platform.http.pagination import ListParams, PaginatedResult

router = APIRouter()


@router.post(
    "",
    response_model=ApiResponse[ProjectResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
)
async def create_project(
    body: ProjectCreate,
    service: ProjectServiceDep,
) -> ApiResponse[ProjectResponse]:
    project = await service.create(body)
    return ApiResponse.ok(ProjectResponse.model_validate(project))


@router.get(
    "",
    response_model=ApiResponse[PaginatedResult[ProjectResponse]],
    summary="List projects",
)
async def list_projects(
    service: ProjectServiceDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    include_deleted: bool = Query(default=False),
    is_active: bool | None = Query(default=None),
) -> ApiResponse[PaginatedResult[ProjectResponse]]:
    params = ListParams(
        limit=limit,
        offset=offset,
        include_deleted=include_deleted,
        is_active=is_active,
    )
    page = await service.list(params)
    return ApiResponse.ok(
        PaginatedResult[ProjectResponse](
            items=[ProjectResponse.model_validate(item) for item in page.items],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )
    )


@router.get(
    "/{project_id}",
    response_model=ApiResponse[ProjectResponse],
    summary="Get a project by id",
)
async def get_project(
    project_id: uuid.UUID,
    service: ProjectServiceDep,
) -> ApiResponse[ProjectResponse]:
    project = await service.get(project_id)
    return ApiResponse.ok(ProjectResponse.model_validate(project))


@router.patch(
    "/{project_id}",
    response_model=ApiResponse[ProjectResponse],
    summary="Update project metadata",
)
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    service: ProjectServiceDep,
) -> ApiResponse[ProjectResponse]:
    project = await service.update(project_id, body)
    return ApiResponse.ok(ProjectResponse.model_validate(project))


@router.patch(
    "/{project_id}/status",
    response_model=ApiResponse[ProjectResponse],
    summary="Toggle project active status",
)
async def update_project_status(
    project_id: uuid.UUID,
    body: ProjectStatusUpdate,
    service: ProjectServiceDep,
) -> ApiResponse[ProjectResponse]:
    project = await service.update_status(project_id, body.is_active)
    return ApiResponse.ok(ProjectResponse.model_validate(project))


@router.delete(
    "/{project_id}",
    response_model=ApiResponse[ProjectResponse],
    summary="Soft-delete a project",
)
async def delete_project(
    project_id: uuid.UUID,
    service: ProjectServiceDep,
) -> ApiResponse[ProjectResponse]:
    project = await service.soft_delete(project_id)
    return ApiResponse.ok(ProjectResponse.model_validate(project))
