"""Document ingestion workflow — parse, chunk, and persist."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk
from app.modules.knowledge.repositories.document_chunk_repository import DocumentChunkRepository
from app.modules.knowledge.repositories.document_repository import DocumentRepository
from app.modules.knowledge.services.chunking_service import ChunkingService
from app.platform.domain.transactions import flush_commit_refresh
from app.platform.providers.contracts.document_parser import BaseDocumentParserProvider
from app.platform.providers.contracts.storage import BaseStorageProvider
from app.platform.providers.errors import ProviderError

logger = structlog.get_logger(__name__)


def build_parsed_text_storage_key(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    version: int,
) -> str:
    return f"{project_id}/{document_id}/parsed/v{version}.txt"


def safe_processing_error(exc: Exception) -> str:
    """Return a client-safe processing error message."""
    if isinstance(exc, ProviderError):
        return exc.message
    return "Document processing failed."


async def read_storage_bytes(storage: BaseStorageProvider, key: str) -> bytes:
    chunks: list[bytes] = []
    async for chunk in storage.get(key):
        chunks.append(chunk)
    return b"".join(chunks)


async def _byte_stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


class DocumentProcessingWorkflow:
    """Orchestrates parse → chunk → persist for a single document."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        storage: BaseStorageProvider,
        parser: BaseDocumentParserProvider,
        chunking: ChunkingService,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._storage = storage
        self._parser = parser
        self._chunking = chunking
        self._repository = DocumentRepository(session, project_id)
        self._chunk_repository = DocumentChunkRepository(session, project_id)

    async def run(self, document_id: uuid.UUID) -> Document | None:
        document = await self._repository.get_by_id(document_id)
        if document is None:
            logger.warning(
                "document_process_skipped_missing",
                project_id=str(self._project_id),
                document_id=str(document_id),
            )
            return None

        if document.status is not DocumentStatus.QUEUED:
            logger.info(
                "document_process_skipped_status",
                project_id=str(self._project_id),
                document_id=str(document_id),
                status=document.status.value,
            )
            return document

        document.status = DocumentStatus.PARSING
        document.error_message = None
        await flush_commit_refresh(self._session, self._repository, document)

        logger.info(
            "document_process_started",
            project_id=str(self._project_id),
            document_id=str(document_id),
        )

        try:
            raw_bytes = await read_storage_bytes(self._storage, document.storage_key)
            parsed = await asyncio.to_thread(
                self._parser.parse,
                data=raw_bytes,
                filename=document.filename,
                content_type=document.content_type,
            )
            parsed_key = build_parsed_text_storage_key(
                document.project_id,
                document.id,
                document.version,
            )
            encoded = parsed.text.encode("utf-8")
            await self._storage.put(
                parsed_key,
                _byte_stream(encoded),
                content_type="text/plain; charset=utf-8",
                size_bytes=len(encoded),
            )

            if document.parsed_text_storage_key and document.parsed_text_storage_key != parsed_key:
                try:
                    await self._storage.delete(document.parsed_text_storage_key)
                except ProviderError as exc:
                    logger.warning(
                        "parsed_text_cleanup_failed",
                        storage_key=document.parsed_text_storage_key,
                        error=str(exc),
                    )

            document.parsed_text_storage_key = parsed_key
            document.page_count = parsed.page_count
            document.parser_name = parsed.parser_name
            document.parser_version = parsed.parser_version
            document.language = parsed.language
            document.error_message = None

            if parsed.warnings:
                logger.info(
                    "document_parse_warnings",
                    project_id=str(self._project_id),
                    document_id=str(document_id),
                    warnings=list(parsed.warnings),
                )

            # Stage marker only — persisted in the single final commit below so a
            # worker crash leaves the document in PARSING (recoverable via reprocess).
            document.status = DocumentStatus.CHUNKING

            await self._chunk_repository.delete_by_document(document.id)
            text_chunks = self._chunking.split(parsed.text, page_count=parsed.page_count)
            chunk_entities = [
                DocumentChunk(
                    project_id=document.project_id,
                    document_id=document.id,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    page_number=chunk.page_number,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    token_count=chunk.token_count,
                    chunk_metadata=chunk.chunk_metadata,
                )
                for chunk in text_chunks
            ]
            self._chunk_repository.bulk_add(chunk_entities)
            await self._chunk_repository.flush()

            document.status = DocumentStatus.CHUNKED
            logger.info(
                "document_chunking_complete",
                project_id=str(self._project_id),
                document_id=str(document_id),
                chunk_count=len(chunk_entities),
            )
        except Exception as exc:
            logger.exception(
                "document_process_failed",
                project_id=str(self._project_id),
                document_id=str(document_id),
            )
            document.status = DocumentStatus.FAILED
            document.error_message = safe_processing_error(exc)

        return await flush_commit_refresh(self._session, self._repository, document)
