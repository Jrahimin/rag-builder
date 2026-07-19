"""Document management HTTP routes — project-scoped uploads."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Form, Query, UploadFile, status

from app.core.http.envelopes import ApiResponse
from app.dependencies.knowledge import DocumentServiceDep
from app.dependencies.retrieval import IndexingServiceDep
from app.modules.knowledge.schemas.chunk import ChunkListParams, ChunkResponse
from app.modules.knowledge.schemas.document import (
    DocumentIngestInput,
    DocumentListParams,
    DocumentResponse,
)
from app.platform.http.pagination import PaginatedResult

router = APIRouter()


async def _file_stream(upload: UploadFile) -> AsyncIterator[bytes]:
    while True:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        yield chunk


@router.post(
    "",
    response_model=ApiResponse[DocumentResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document",
)
async def upload_document(
    project_id: uuid.UUID,
    file: UploadFile,
    service: DocumentServiceDep,
    ocr_lang: str | None = Form(default=None),
) -> ApiResponse[DocumentResponse]:
    del project_id
    ingest = DocumentIngestInput(
        filename=file.filename or "upload",
        content_type=file.content_type,
        stream=_file_stream(file),
        ocr_lang=ocr_lang,
    )
    document = await service.upload(ingest)
    return ApiResponse.ok(DocumentResponse.model_validate(document))


@router.get(
    "",
    response_model=ApiResponse[PaginatedResult[DocumentResponse]],
    summary="List documents in a project",
)
async def list_documents(
    project_id: uuid.UUID,
    service: DocumentServiceDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    include_deleted: bool = Query(default=False),
) -> ApiResponse[PaginatedResult[DocumentResponse]]:
    del project_id
    params = DocumentListParams(
        limit=limit,
        offset=offset,
        include_deleted=include_deleted,
    )
    page = await service.list(params)
    return ApiResponse.ok(
        PaginatedResult[DocumentResponse](
            items=[DocumentResponse.model_validate(item) for item in page.items],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )
    )


@router.get(
    "/{document_id}",
    response_model=ApiResponse[DocumentResponse],
    summary="Get document metadata by id",
)
async def get_document(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    service: DocumentServiceDep,
) -> ApiResponse[DocumentResponse]:
    del project_id
    document = await service.get(document_id)
    return ApiResponse.ok(DocumentResponse.model_validate(document))


@router.delete(
    "/{document_id}",
    response_model=ApiResponse[DocumentResponse],
    summary="Stage a reversible document deletion",
    status_code=status.HTTP_202_ACCEPTED,
)
async def delete_document(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    service: DocumentServiceDep,
) -> ApiResponse[DocumentResponse]:
    del project_id
    document = await service.soft_delete(document_id)
    return ApiResponse.ok(DocumentResponse.model_validate(document))


@router.delete(
    "/{document_id}/purge",
    response_model=ApiResponse[DocumentResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Irreversibly purge a document and every retained artifact",
)
async def purge_document(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    service: DocumentServiceDep,
) -> ApiResponse[DocumentResponse]:
    del project_id
    document = await service.purge(document_id)
    return ApiResponse.ok(DocumentResponse.model_validate(document))


@router.get(
    "/{document_id}/chunks",
    response_model=ApiResponse[PaginatedResult[ChunkResponse]],
    summary="List text chunks for a document",
)
async def list_document_chunks(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    service: DocumentServiceDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[PaginatedResult[ChunkResponse]]:
    del project_id
    params = ChunkListParams(limit=limit, offset=offset)
    page = await service.list_chunks(document_id, params)
    return ApiResponse.ok(
        PaginatedResult[ChunkResponse](
            items=[ChunkResponse.model_validate(item) for item in page.items],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )
    )


@router.post(
    "/{document_id}/reprocess",
    response_model=ApiResponse[DocumentResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-enqueue document parsing",
)
async def reprocess_document(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    service: DocumentServiceDep,
    ocr_lang: str | None = Query(default=None),
) -> ApiResponse[DocumentResponse]:
    del project_id
    document = await service.reprocess(document_id, ocr_lang=ocr_lang)
    return ApiResponse.ok(DocumentResponse.model_validate(document))


@router.post(
    "/{document_id}/embed",
    response_model=ApiResponse[DocumentResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue document embedding",
)
async def embed_document(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    service: IndexingServiceDep,
) -> ApiResponse[DocumentResponse]:
    del project_id
    document = await service.enqueue_embed(document_id)
    return ApiResponse.ok(DocumentResponse.model_validate(document))


@router.post(
    "/{document_id}/index",
    response_model=ApiResponse[DocumentResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue retrieval indexing",
)
async def index_document(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    service: IndexingServiceDep,
) -> ApiResponse[DocumentResponse]:
    del project_id
    document = await service.enqueue_index(document_id)
    return ApiResponse.ok(DocumentResponse.model_validate(document))
