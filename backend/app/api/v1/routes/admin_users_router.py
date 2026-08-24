"""Platform operator account management (cookie session required)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status

from app.core.http.envelopes import ApiResponse
from app.dependencies.admin_auth import AdminUserServiceDep, require_super_admin
from app.modules.admin_auth.schemas import AdminUserCreate, AdminUserResponse, AdminUserStatusUpdate
from app.platform.http.pagination import ListParams, PaginatedResult

router = APIRouter(dependencies=[Depends(require_super_admin)])


@router.post(
    "",
    response_model=ApiResponse[AdminUserResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create an Admin operator",
)
async def create_admin_user(
    body: AdminUserCreate,
    service: AdminUserServiceDep,
) -> ApiResponse[AdminUserResponse]:
    admin = await service.create(body)
    return ApiResponse.ok(AdminUserResponse.model_validate(admin))


@router.get(
    "",
    response_model=ApiResponse[PaginatedResult[AdminUserResponse]],
    summary="List platform operators",
)
async def list_admin_users(
    service: AdminUserServiceDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    include_deleted: bool = Query(default=False),
    is_active: bool | None = Query(default=None),
) -> ApiResponse[PaginatedResult[AdminUserResponse]]:
    page = await service.list(
        ListParams(
            limit=limit,
            offset=offset,
            include_deleted=include_deleted,
            is_active=is_active,
        )
    )
    return ApiResponse.ok(
        PaginatedResult[AdminUserResponse](
            items=[AdminUserResponse.model_validate(item) for item in page.items],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )
    )


@router.get(
    "/{admin_user_id}",
    response_model=ApiResponse[AdminUserResponse],
    summary="Get a platform operator by id",
)
async def get_admin_user(
    admin_user_id: uuid.UUID,
    service: AdminUserServiceDep,
    include_deleted: bool = Query(default=False),
) -> ApiResponse[AdminUserResponse]:
    admin = await service.get(admin_user_id, include_deleted=include_deleted)
    return ApiResponse.ok(AdminUserResponse.model_validate(admin))


@router.put(
    "/{admin_user_id}/status",
    response_model=ApiResponse[AdminUserResponse],
    summary="Set Admin active status explicitly",
)
async def set_admin_user_status(
    admin_user_id: uuid.UUID,
    body: AdminUserStatusUpdate,
    service: AdminUserServiceDep,
) -> ApiResponse[AdminUserResponse]:
    admin = await service.set_status(admin_user_id, is_active=body.is_active)
    return ApiResponse.ok(AdminUserResponse.model_validate(admin))


@router.delete(
    "/{admin_user_id}",
    response_model=ApiResponse[AdminUserResponse],
    summary="Soft-delete an Admin operator",
)
async def delete_admin_user(
    admin_user_id: uuid.UUID,
    service: AdminUserServiceDep,
) -> ApiResponse[AdminUserResponse]:
    admin = await service.soft_delete(admin_user_id)
    return ApiResponse.ok(AdminUserResponse.model_validate(admin))


@router.post(
    "/{admin_user_id}/restore",
    response_model=ApiResponse[AdminUserResponse],
    summary="Restore a deleted Admin in a disabled state",
)
async def restore_admin_user(
    admin_user_id: uuid.UUID,
    service: AdminUserServiceDep,
) -> ApiResponse[AdminUserResponse]:
    admin = await service.restore(admin_user_id)
    return ApiResponse.ok(AdminUserResponse.model_validate(admin))
