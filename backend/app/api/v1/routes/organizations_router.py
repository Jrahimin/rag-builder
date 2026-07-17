"""Organization management HTTP routes (admin API key required)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status

from app.core.http.envelopes import ApiResponse
from app.dependencies.auth import require_admin_api_key
from app.dependencies.organizations import ApiKeyServiceDep, OrganizationServiceDep
from app.modules.organizations.schemas.api_key import (
    ApiKeyCreate,
    ApiKeyResponse,
    ApiKeySecretResponse,
)
from app.modules.organizations.schemas.organization import (
    OrganizationCreate,
    OrganizationResponse,
    OrganizationUpdate,
)
from app.platform.http.pagination import ListParams, PaginatedResult

router = APIRouter(dependencies=[Depends(require_admin_api_key)])


@router.post(
    "",
    response_model=ApiResponse[OrganizationResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create an organization",
)
async def create_organization(
    body: OrganizationCreate,
    service: OrganizationServiceDep,
) -> ApiResponse[OrganizationResponse]:
    organization = await service.create(body)
    return ApiResponse.ok(OrganizationResponse.model_validate(organization))


@router.get(
    "",
    response_model=ApiResponse[PaginatedResult[OrganizationResponse]],
    summary="List organizations",
)
async def list_organizations(
    service: OrganizationServiceDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    include_deleted: bool = Query(default=False),
    is_active: bool | None = Query(default=None),
) -> ApiResponse[PaginatedResult[OrganizationResponse]]:
    params = ListParams(
        limit=limit,
        offset=offset,
        include_deleted=include_deleted,
        is_active=is_active,
    )
    page = await service.list(params)
    return ApiResponse.ok(
        PaginatedResult[OrganizationResponse](
            items=[OrganizationResponse.model_validate(item) for item in page.items],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )
    )


@router.get(
    "/{organization_id}",
    response_model=ApiResponse[OrganizationResponse],
    summary="Get an organization by id",
)
async def get_organization(
    organization_id: uuid.UUID,
    service: OrganizationServiceDep,
) -> ApiResponse[OrganizationResponse]:
    organization = await service.get(organization_id)
    return ApiResponse.ok(OrganizationResponse.model_validate(organization))


@router.patch(
    "/{organization_id}",
    response_model=ApiResponse[OrganizationResponse],
    summary="Update organization metadata",
)
async def update_organization(
    organization_id: uuid.UUID,
    body: OrganizationUpdate,
    service: OrganizationServiceDep,
) -> ApiResponse[OrganizationResponse]:
    organization = await service.update(organization_id, body)
    return ApiResponse.ok(OrganizationResponse.model_validate(organization))


@router.patch(
    "/{organization_id}/status",
    response_model=ApiResponse[OrganizationResponse],
    summary="Toggle organization active status",
)
async def toggle_organization_status(
    organization_id: uuid.UUID,
    service: OrganizationServiceDep,
) -> ApiResponse[OrganizationResponse]:
    organization = await service.toggle_status(organization_id)
    return ApiResponse.ok(OrganizationResponse.model_validate(organization))


@router.delete(
    "/{organization_id}",
    response_model=ApiResponse[OrganizationResponse],
    summary="Soft-delete an organization",
)
async def delete_organization(
    organization_id: uuid.UUID,
    service: OrganizationServiceDep,
) -> ApiResponse[OrganizationResponse]:
    organization = await service.soft_delete(organization_id)
    return ApiResponse.ok(OrganizationResponse.model_validate(organization))


@router.post(
    "/{organization_id}/api-keys",
    response_model=ApiResponse[ApiKeySecretResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a named API key",
)
async def create_api_key(
    organization_id: uuid.UUID,
    body: ApiKeyCreate,
    service: ApiKeyServiceDep,
) -> ApiResponse[ApiKeySecretResponse]:
    api_key, secret = await service.create(organization_id, body)
    return ApiResponse.ok(
        ApiKeySecretResponse(
            **ApiKeyResponse.model_validate(api_key).model_dump(),
            secret=secret,
        )
    )


@router.get(
    "/{organization_id}/api-keys",
    response_model=ApiResponse[PaginatedResult[ApiKeyResponse]],
    summary="List organization API keys",
)
async def list_api_keys(
    organization_id: uuid.UUID,
    service: ApiKeyServiceDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[PaginatedResult[ApiKeyResponse]]:
    page = await service.list(organization_id, limit=limit, offset=offset)
    return ApiResponse.ok(
        PaginatedResult[ApiKeyResponse](
            items=[ApiKeyResponse.model_validate(item) for item in page.items],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )
    )


@router.post(
    "/{organization_id}/api-keys/{key_id}/rotate",
    response_model=ApiResponse[ApiKeySecretResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Rotate an API key",
)
async def rotate_api_key(
    organization_id: uuid.UUID,
    key_id: uuid.UUID,
    service: ApiKeyServiceDep,
    revoke_old: bool = Query(default=False),
) -> ApiResponse[ApiKeySecretResponse]:
    api_key, secret = await service.rotate(
        organization_id,
        key_id,
        revoke_old=revoke_old,
    )
    return ApiResponse.ok(
        ApiKeySecretResponse(
            **ApiKeyResponse.model_validate(api_key).model_dump(),
            secret=secret,
        )
    )


@router.delete(
    "/{organization_id}/api-keys/{key_id}",
    response_model=ApiResponse[ApiKeyResponse],
    summary="Revoke an API key",
)
async def revoke_api_key(
    organization_id: uuid.UUID,
    key_id: uuid.UUID,
    service: ApiKeyServiceDep,
) -> ApiResponse[ApiKeyResponse]:
    api_key = await service.revoke(organization_id, key_id)
    return ApiResponse.ok(ApiKeyResponse.model_validate(api_key))
