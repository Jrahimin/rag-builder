"""Document business orchestration — upload, read, list, soft delete."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, PayloadTooLargeError, ServiceUnavailableError
from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk
from app.modules.knowledge.repositories.document_chunk_repository import DocumentChunkRepository
from app.modules.knowledge.repositories.document_repository import DocumentRepository
from app.modules.knowledge.schemas.chunk import ChunkListParams
from app.modules.knowledge.schemas.document import DocumentIngestInput, DocumentListParams
from app.platform.domain.lifecycle_service import (
    get_or_raise,
    list_paginated,
    require_not_deleted,
    soft_delete,
)
from app.platform.domain.transactions import flush_commit_refresh
from app.platform.http.pagination import ListParams, PaginatedResult
from app.platform.jobs.contracts import JobDefinition, JobQueue
from app.platform.jobs.errors import JobEnqueueError
from app.platform.jobs.names import DOCUMENT_PROCESS
from app.platform.providers.contracts.storage import BaseStorageProvider
from app.platform.providers.errors import ProviderError

logger = structlog.get_logger(__name__)

type EnsureProjectFn = Callable[[], Awaitable[None]]
type OnDocumentDeleteFn = Callable[[Document], Awaitable[None]]

_UNSAFE_FILENAME = re.compile(r"[^\w.\-]+")
_NOT_FOUND = {"message": "Document not found.", "code": "document_not_found"}
_DELETED = {"message": "Cannot process a deleted document.", "code": "document_deleted"}

# Uploads larger than this spill from memory to a temp file while hashing.
_SPOOL_MAX_MEMORY_BYTES = 4 * 1024 * 1024
_REPLAY_CHUNK_BYTES = 64 * 1024


def safe_filename(name: str) -> str:
    """Return a basename safe for object storage keys."""
    base = os.path.basename(name).strip() or "upload"
    cleaned = _UNSAFE_FILENAME.sub("_", base)
    return cleaned[:255]


def build_storage_key(project_id: uuid.UUID, document_id: uuid.UUID, filename: str) -> str:
    return f"{project_id}/{document_id}/{safe_filename(filename)}"


def _duplicate_content() -> ConflictError:
    return ConflictError(
        message="A document with identical content already exists in this project.",
        code="document_content_duplicate",
    )


class DocumentService:
    """Orchestrates document upload and lifecycle within a Project."""

    def __init__(
        self,
        session: AsyncSession,
        repository: DocumentRepository,
        storage: BaseStorageProvider,
        job_queue: JobQueue,
        *,
        ensure_project: EnsureProjectFn,
        max_upload_bytes: int,
        on_document_delete: OnDocumentDeleteFn | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._storage = storage
        self._job_queue = job_queue
        self._ensure_project = ensure_project
        self._max_upload_bytes = max_upload_bytes
        self._on_document_delete = on_document_delete

    async def upload(self, data: DocumentIngestInput) -> Document:
        await self._ensure_project()

        digest, size_bytes, replay_stream = await self._hash_stream(data.stream)
        if await self._repository.exists_by_content_sha256(digest):
            raise _duplicate_content()

        document_id = uuid.uuid4()
        storage_key = build_storage_key(self._repository.project_id, document_id, data.filename)

        document = Document(
            id=document_id,
            project_id=self._repository.project_id,
            filename=safe_filename(data.filename),
            content_type=data.content_type,
            size_bytes=size_bytes,
            storage_key=storage_key,
            content_sha256=digest,
            status=DocumentStatus.UPLOADED,
            version=1,
        )
        self._repository.add(document)

        try:
            await self._storage.put(
                storage_key,
                replay_stream,
                content_type=data.content_type,
                size_bytes=size_bytes,
            )
        except ProviderError as exc:
            await self._session.rollback()
            raise ServiceUnavailableError(
                message="Object storage is unavailable.",
                code="storage_unavailable",
            ) from exc

        try:
            document = await flush_commit_refresh(
                self._session,
                self._repository,
                document,
                on_integrity=_duplicate_content,
            )
        except ConflictError:
            await self._storage.delete(storage_key)
            raise

        return await self._enqueue_processing(document)

    async def reprocess(self, document_id: uuid.UUID) -> Document:
        document = await get_or_raise(
            self._repository,
            document_id,
            message=_NOT_FOUND["message"],
            code=_NOT_FOUND["code"],
            include_deleted=True,
        )
        require_not_deleted(
            document,
            message=_DELETED["message"],
            code=_DELETED["code"],
        )

        document.version += 1
        document.error_message = None
        document.parser_name = None
        document.parser_version = None
        document.page_count = None
        document.language = None

        return await self._enqueue_processing(document)

    async def _enqueue_processing(self, document: Document) -> Document:
        document.status = DocumentStatus.QUEUED
        document = await flush_commit_refresh(self._session, self._repository, document)

        try:
            await self._job_queue.enqueue(
                JobDefinition(
                    name=DOCUMENT_PROCESS,
                    project_id=document.project_id,
                    payload={"document_id": str(document.id)},
                    idempotency_key=(
                        f"document.process:{document.project_id}:{document.id}:v{document.version}"
                    ),
                )
            )
        except JobEnqueueError as exc:
            document.status = DocumentStatus.UPLOADED
            await flush_commit_refresh(self._session, self._repository, document)
            raise ServiceUnavailableError(
                message="Background processing queue is unavailable.",
                code="job_queue_unavailable",
            ) from exc

        refreshed = await self._repository.get_by_id(document.id, include_deleted=True)
        return refreshed if refreshed is not None else document

    async def get(self, document_id: uuid.UUID) -> Document:
        return await get_or_raise(
            self._repository,
            document_id,
            message=_NOT_FOUND["message"],
            code=_NOT_FOUND["code"],
        )

    async def list(self, params: DocumentListParams) -> PaginatedResult[Document]:
        list_params = ListParams(
            limit=params.limit,
            offset=params.offset,
            include_deleted=params.include_deleted,
            is_active=None,
        )
        return await list_paginated(self._repository, list_params)

    async def list_chunks(
        self,
        document_id: uuid.UUID,
        params: ChunkListParams,
    ) -> PaginatedResult[DocumentChunk]:
        await self.get(document_id)
        chunk_repository = DocumentChunkRepository(
            self._session,
            self._repository.project_id,
        )
        items = await chunk_repository.list_by_document(
            document_id,
            limit=params.limit,
            offset=params.offset,
        )
        total = await chunk_repository.count_by_document(document_id)
        return PaginatedResult(
            items=items,
            total=total,
            limit=params.limit,
            offset=params.offset,
        )

    async def soft_delete(self, document_id: uuid.UUID) -> Document:
        document = await get_or_raise(
            self._repository,
            document_id,
            message=_NOT_FOUND["message"],
            code=_NOT_FOUND["code"],
        )
        if self._on_document_delete is not None:
            await self._on_document_delete(document)
        document = await soft_delete(
            self._session,
            self._repository,
            document_id,
            not_found_message=_NOT_FOUND["message"],
            not_found_code=_NOT_FOUND["code"],
        )
        chunk_repository = DocumentChunkRepository(
            self._session,
            self._repository.project_id,
        )
        await chunk_repository.delete_by_document(document.id)
        try:
            await self._storage.delete(document.storage_key)
            if document.parsed_text_storage_key:
                await self._storage.delete(document.parsed_text_storage_key)
        except ProviderError as exc:
            logger.warning(
                "storage_delete_failed",
                document_id=str(document.id),
                storage_key=document.storage_key,
                error=str(exc),
            )
        return document

    async def _hash_stream(
        self,
        stream: AsyncIterator[bytes],
    ) -> tuple[str, int, AsyncIterator[bytes]]:
        """Hash the upload while spooling it to a temp file (bounded memory)."""
        hasher = hashlib.sha256()
        # Not a context manager: the spool outlives this method and is closed by
        # the replay generator (or the except branch below on failure).
        spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX_MEMORY_BYTES)  # noqa: SIM115
        size_bytes = 0

        try:
            async for chunk in stream:
                size_bytes += len(chunk)
                if size_bytes > self._max_upload_bytes:
                    raise PayloadTooLargeError(
                        message=(
                            f"Upload exceeds the maximum allowed size of "
                            f"{self._max_upload_bytes} bytes."
                        ),
                        code="document_too_large",
                    )
                hasher.update(chunk)
                spool.write(chunk)
        except BaseException:
            spool.close()
            raise

        spool.seek(0)

        async def replay() -> AsyncIterator[bytes]:
            try:
                while True:
                    data = spool.read(_REPLAY_CHUNK_BYTES)
                    if not data:
                        break
                    yield data
            finally:
                spool.close()

        return hasher.hexdigest(), size_bytes, replay()
