"""Project-scoped semantic search routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.core.http.envelopes import ApiResponse
from app.dependencies.retrieval import SearchServiceDep
from app.modules.retrieval.schemas.search import SearchRequest, SearchResponse

router = APIRouter()


@router.post(
    "/search",
    response_model=ApiResponse[SearchResponse],
    status_code=status.HTTP_200_OK,
    summary="Search over indexed documents (semantic or hybrid)",
)
async def search_project(
    project_id: uuid.UUID,
    body: SearchRequest,
    service: SearchServiceDep,
) -> ApiResponse[SearchResponse]:
    del project_id
    response = await service.search(body)
    return ApiResponse.ok(response)
