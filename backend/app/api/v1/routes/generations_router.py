"""Contextual generation HTTP routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Request, status

from app.core.http.envelopes import ApiResponse, ResponseMeta
from app.dependencies.generation import GenerationServiceDep
from app.modules.generation.schemas.generation import (
    GenerationCreateRequest,
    GenerationResponse,
)

router = APIRouter()

IdempotencyKeyHeader = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
        description="Replay key scoped to the Project and normalized request payload.",
    ),
]


def _response_meta(request: Request) -> ResponseMeta:
    return ResponseMeta(
        request_id=getattr(request.state, "request_id", None),
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post(
    "",
    response_model=ApiResponse[GenerationResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Generate a validated response from caller-provided context",
)
async def create_generation(
    project_id: uuid.UUID,
    body: GenerationCreateRequest,
    request: Request,
    service: GenerationServiceDep,
    idempotency_key: IdempotencyKeyHeader = None,
) -> ApiResponse[GenerationResponse]:
    del project_id
    result = await service.create(
        body,
        idempotency_key=idempotency_key,
        request_id=request.state.request_id,
        trace_id=request.state.trace_id,
    )
    return ApiResponse.ok(result, meta=_response_meta(request))


@router.get(
    "/{generation_id}",
    response_model=ApiResponse[GenerationResponse],
    summary="Get a contextual generation trace by id",
)
async def get_generation(
    project_id: uuid.UUID,
    generation_id: uuid.UUID,
    request: Request,
    service: GenerationServiceDep,
) -> ApiResponse[GenerationResponse]:
    del project_id
    result = await service.get(generation_id)
    return ApiResponse.ok(result, meta=_response_meta(request))
