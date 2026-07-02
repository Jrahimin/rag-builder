"""Project-scoped semantic search routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.dependencies.retrieval import SearchServiceDep
from app.modules.retrieval.schemas.search import SearchRequest, SearchResponse
from app.platform.http.envelopes import ApiResponse

router = APIRouter()


@router.post(
    "/search",
    response_model=ApiResponse[SearchResponse],
    status_code=status.HTTP_200_OK,
    summary="Semantic search over indexed documents",
)
async def search_project(
    project_id: uuid.UUID,
    body: SearchRequest,
    service: SearchServiceDep,
) -> ApiResponse[SearchResponse]:
    del project_id
    response = await service.search(body)
    return ApiResponse.ok(response)
