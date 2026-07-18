"""Document business orchestration — upload, read, list, soft delete."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import uuid
from collections.abc import AsyncIterator
from typing import BinaryIO, cast

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, PayloadTooLargeError, ServiceUnavailableError
from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk
from app.modules.knowledge.repositories.document_chunk_repository import DocumentChunkRepository
from app.modules.knowledge.repositories.document_repository import DocumentRepository
from app.modules.knowledge.schemas.chunk import ChunkListParams
from app.modules.knowledge.schemas.document import DocumentIngestInput, DocumentListParams
from app.modules.knowledge.services.file_validation_service import FileValidationService
from app.platform.audit.contracts import AuditActorType, AuditEventType, AuditOutcome, AuditRecorder
from app.platform.domain.lifecycle_service import (
    get_or_raise,
    list_paginated,
    require_not_deleted,
)
from app.platform.domain.ocr_language import normalize_stored_ocr_lang
from app.platform.http.pagination import ListParams, PaginatedResult
from app.platform.jobs.contracts import (
    DurableJobSubmitter,
    JobConfiguration,
    JobDefinition,
    RetryPolicy,
)
from app.platform.jobs.names import DOCUMENT_DELETE, DOCUMENT_PROCESS, DOCUMENT_PURGE
from app.platform.providers.contracts.malware_scanner import BaseMalwareScanner
from app.platform.providers.contracts.storage import BaseStorageProvider
from app.platform.providers.errors import ProviderError

logger = structlog.get_logger(__name__)

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
        job_submitter: DurableJobSubmitter,
        job_configuration: JobConfiguration,
        *,
        job_max_attempts: int,
        max_upload_bytes: int,
        malware_scanner: BaseMalwareScanner | None = None,
        file_validator: FileValidationService | None = None,
        audit: AuditRecorder | None = None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._storage = storage
        if malware_scanner is None:
            from app.platform.providers.implementations.malware_scanner_provider import (
                DisabledMalwareScanner,
            )

            malware_scanner = DisabledMalwareScanner()
        self._malware_scanner = malware_scanner
        self._file_validator = file_validator or FileValidationService()
        self._job_submitter = job_submitter
        self._job_configuration = job_configuration
        self._job_max_attempts = job_max_attempts
        self._max_upload_bytes = max_upload_bytes
        self._audit = audit

    async def upload(self, data: DocumentIngestInput) -> Document:
        digest, size_bytes, spool = await self._hash_stream(data.stream)
        try:
            content_type = self._file_validator.validate(
                filename=data.filename, content_type=data.content_type, file=spool
            )
            try:
                verdict = await self._malware_scanner.scan(self._spool_stream(spool))
            except ProviderError as exc:
                raise ServiceUnavailableError(
                    message="Malware scanning is unavailable.",
                    code="malware_scanner_unavailable",
                ) from exc
            if not verdict.clean:
                from app.core.exceptions import BadRequestError

                raise BadRequestError(
                    message="The uploaded file failed malware scanning.",
                    code="document_malware_detected",
                    context={"scanner": verdict.scanner, "signature": verdict.signature},
                )
            if await self._repository.exists_by_content_sha256(digest):
                raise _duplicate_content()

            document_id = uuid.uuid4()
            storage_key = build_storage_key(self._repository.project_id, document_id, data.filename)

            document = Document(
                id=document_id,
                project_id=self._repository.project_id,
                filename=safe_filename(data.filename),
                content_type=content_type,
                size_bytes=size_bytes,
                storage_key=storage_key,
                content_sha256=digest,
                status=DocumentStatus.UPLOADED,
                version=1,
                ocr_lang=normalize_stored_ocr_lang(data.ocr_lang),
            )
            self._repository.add(document)

            try:
                await self._storage.put(
                    storage_key,
                    self._spool_stream(spool),
                    content_type=content_type,
                    size_bytes=size_bytes,
                )
            except ProviderError as exc:
                await self._session.rollback()
                raise ServiceUnavailableError(
                    message="Object storage is unavailable.",
                    code="storage_unavailable",
                ) from exc

            try:
                await self._repository.flush()
                return await self._stage_processing(document, operation="ingest")
            except IntegrityError as exc:
                await self._session.rollback()
                await self._storage.delete(storage_key)
                raise _duplicate_content() from exc
            except Exception:
                await self._session.rollback()
                await self._storage.delete(storage_key)
                raise
        finally:
            spool.close()

    async def reprocess(
        self,
        document_id: uuid.UUID,
        *,
        ocr_lang: str | None = None,
    ) -> Document:
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
        if document.status in {DocumentStatus.DELETING, DocumentStatus.PURGING}:
            raise ConflictError(
                message="A destructive document lifecycle job is already pending.",
                code="document_lifecycle_pending",
            )

        document.version += 1
        document.error_message = None
        document.parser_name = None
        document.parser_version = None
        document.accepted_parser = None
        document.parse_quality_score = None
        document.extraction_method = None
        document.page_count = None
        document.language = None
        document.language_confidence = None
        if ocr_lang is not None:
            document.ocr_lang = normalize_stored_ocr_lang(ocr_lang)

        return await self._stage_processing(document, operation="reprocess")

    async def _stage_processing(self, document: Document, *, operation: str) -> Document:
        document.status = DocumentStatus.QUEUED
        submission = await self._job_submitter.stage(
            JobDefinition(
                name=DOCUMENT_PROCESS,
                project_id=document.project_id,
                document_id=document.id,
                payload={"document_version": document.version, "operation": operation},
                idempotency_key=(
                    f"document.process:{document.project_id}:{document.id}:v{document.version}"
                ),
                retry=RetryPolicy(max_attempts=self._job_max_attempts),
            ),
            self._job_configuration,
        )
        await self._session.commit()
        await self._session.refresh(document)
        await self._job_submitter.dispatch(submission.job_id)
        document = await self._repository.get_by_id(document.id, include_deleted=True) or document
        document.__dict__["job_id"] = submission.job_id
        return document

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
        document = await self.get(document_id)
        chunk_repository = DocumentChunkRepository(
            self._session,
            self._repository.project_id,
        )
        items = await chunk_repository.list_by_document(
            document_id,
            limit=params.limit,
            offset=params.offset,
            document_version=document.version,
        )
        total = await chunk_repository.count_by_document(
            document_id, document_version=document.version
        )
        return PaginatedResult(
            items=items,
            total=total,
            limit=params.limit,
            offset=params.offset,
        )

    async def soft_delete(self, document_id: uuid.UUID) -> Document:
        return await self._stage_destructive(document_id, purge=False)

    async def purge(self, document_id: uuid.UUID) -> Document:
        return await self._stage_destructive(document_id, purge=True)

    async def _stage_destructive(self, document_id: uuid.UUID, *, purge: bool) -> Document:
        document = await get_or_raise(
            self._repository,
            document_id,
            message=_NOT_FOUND["message"],
            code=_NOT_FOUND["code"],
            include_deleted=True,
        )
        name = DOCUMENT_PURGE if purge else DOCUMENT_DELETE
        submission = await self._job_submitter.stage(
            JobDefinition(
                name=name,
                project_id=document.project_id,
                document_id=document.id,
                payload={
                    "document_version": document.version,
                    "exclude_document_id": str(document.id),
                    "auto_activate": True,
                },
                idempotency_key=f"{name}:{document.project_id}:{document.id}:v{document.version}",
                retry=RetryPolicy(max_attempts=self._job_max_attempts),
            ),
            self._job_configuration,
        )
        if submission.created:
            document.status = DocumentStatus.PURGING if purge else DocumentStatus.DELETING
        if self._audit is not None:
            self._audit.record(
                event_type=(
                    AuditEventType.DOCUMENT_PURGE_REQUESTED
                    if purge
                    else AuditEventType.DOCUMENT_DELETE_REQUESTED
                ),
                actor_type=AuditActorType.OPERATOR,
                resource_type="document",
                resource_id=document.id,
                outcome=AuditOutcome.SUCCESS,
                detail={"job_id": str(submission.job_id), "document_version": document.version},
            )
        await self._session.commit()
        await self._session.refresh(document)
        await self._job_submitter.dispatch(submission.job_id)
        document = await self._repository.get_by_id(document.id, include_deleted=True) or document
        document.__dict__["job_id"] = submission.job_id
        return document

    async def _hash_stream(
        self,
        stream: AsyncIterator[bytes],
    ) -> tuple[str, int, BinaryIO]:
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
        return hasher.hexdigest(), size_bytes, cast(BinaryIO, spool)

    @staticmethod
    async def _spool_stream(spool: BinaryIO) -> AsyncIterator[bytes]:
        spool.seek(0)
        while True:
            data = spool.read(_REPLAY_CHUNK_BYTES)
            if not data:
                break
            yield data
