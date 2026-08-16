"""Knowledge-owned source metadata lifecycle routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.core.http.envelopes import ApiResponse
from app.dependencies.knowledge import SourceMetadataServiceDep
from app.modules.knowledge.schemas.source_metadata import (
    ActiveSourceResponse,
    SourceActivationResponse,
    SourceRevisionActivation,
    SourceRevisionCreate,
    SourceRevisionCreateResponse,
    SourceRevisionResponse,
    SourceStateResponse,
)

router = APIRouter()


@router.get("", response_model=ApiResponse[SourceStateResponse])
async def get_source_state(
    project_id: uuid.UUID,
    service: SourceMetadataServiceDep,
    generation: int | None = Query(default=None, ge=0),
) -> ApiResponse[SourceStateResponse]:
    del project_id
    return ApiResponse.ok(await service.state(generation))


@router.get(
    "/documents/{document_id}",
    response_model=ApiResponse[ActiveSourceResponse],
)
async def get_active_source_metadata(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    service: SourceMetadataServiceDep,
) -> ApiResponse[ActiveSourceResponse]:
    del project_id
    return ApiResponse.ok(await service.active_for_document(document_id))


@router.get(
    "/documents/{document_id}/revisions",
    response_model=ApiResponse[list[SourceRevisionResponse]],
)
async def list_source_revisions(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    service: SourceMetadataServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[list[SourceRevisionResponse]]:
    del project_id
    return ApiResponse.ok(await service.history(document_id, limit=limit, offset=offset))


@router.post(
    "/documents/{document_id}/revisions",
    response_model=ApiResponse[SourceRevisionCreateResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_source_revision(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    body: SourceRevisionCreate,
    service: SourceMetadataServiceDep,
) -> ApiResponse[SourceRevisionCreateResponse]:
    del project_id
    return ApiResponse.ok(await service.create_revision(document_id, body))


@router.get(
    "/revisions/{revision_id}",
    response_model=ApiResponse[SourceRevisionResponse],
)
async def get_source_revision(
    project_id: uuid.UUID,
    revision_id: uuid.UUID,
    service: SourceMetadataServiceDep,
) -> ApiResponse[SourceRevisionResponse]:
    del project_id
    return ApiResponse.ok(await service.get_revision(revision_id))


@router.post(
    "/revisions/{revision_id}/activate",
    response_model=ApiResponse[SourceActivationResponse],
)
async def activate_source_revision(
    project_id: uuid.UUID,
    revision_id: uuid.UUID,
    body: SourceRevisionActivation,
    service: SourceMetadataServiceDep,
) -> ApiResponse[SourceActivationResponse]:
    del project_id
    activation = await service.activate(revision_id, reason=body.reason)
    return ApiResponse.ok(SourceActivationResponse.model_validate(activation))


@router.get(
    "/activations",
    response_model=ApiResponse[list[SourceActivationResponse]],
)
async def list_source_activations(
    project_id: uuid.UUID,
    service: SourceMetadataServiceDep,
    document_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[list[SourceActivationResponse]]:
    del project_id
    rows = await service.activation_history(
        document_id=document_id,
        limit=limit,
        offset=offset,
    )
    return ApiResponse.ok([SourceActivationResponse.model_validate(row) for row in rows])
