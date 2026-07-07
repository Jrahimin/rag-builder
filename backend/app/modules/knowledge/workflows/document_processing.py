"""Document ingestion workflow — parse, chunk, and persist."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.knowledge.domain.document_storage_keys import (
    build_parsed_json_storage_key,
    build_parsed_text_storage_key,
)
from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk
from app.modules.knowledge.repositories.document_chunk_repository import DocumentChunkRepository
from app.modules.knowledge.repositories.document_repository import DocumentRepository
from app.modules.knowledge.services.chunking.models import ChunkingRunMetadata
from app.modules.knowledge.services.chunking_service import ChunkingService
from app.platform.domain.transactions import flush_commit_refresh
from app.platform.providers.contracts.document_parser import BaseDocumentParserProvider, ParsedDocument
from app.platform.providers.contracts.storage import BaseStorageProvider
from app.platform.providers.errors import ProviderError

logger = structlog.get_logger(__name__)


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
    """Orchestrates parse -> chunk -> persist for a single document."""

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
                ocr_lang=document.ocr_lang,
            )
            parsed_key = build_parsed_text_storage_key(
                document.project_id,
                document.id,
                document.version,
            )
            parsed_json_key = build_parsed_json_storage_key(
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
            document.accepted_parser = parsed.structure_hints.get("accepted_parser")
            parse_quality_score = parsed.parse_quality_score
            if parse_quality_score is None:
                raw_score = parsed.structure_hints.get("parse_quality_score")
                parse_quality_score = float(raw_score) if raw_score is not None else None
            document.parse_quality_score = parse_quality_score
            document.extraction_method = parsed.structure_hints.get("extraction_method")
            document.language = parsed.language
            language_confidence = parsed.structure_hints.get("language_confidence")
            document.language_confidence = (
                float(language_confidence) if language_confidence is not None else None
            )
            document.error_message = None

            if parsed.warnings:
                logger.info(
                    "document_parse_warnings",
                    project_id=str(self._project_id),
                    document_id=str(document_id),
                    warnings=list(parsed.warnings),
                )

            extraction_summary = parsed.structure_hints.get("extraction_summary")
            if isinstance(extraction_summary, dict):
                logger.info(
                    "document_parse_quality",
                    project_id=str(self._project_id),
                    document_id=str(document_id),
                    accepted_parser=extraction_summary.get("accepted_parser"),
                    parse_quality_score=extraction_summary.get("parse_quality_score"),
                    extraction_method=extraction_summary.get("extraction_method"),
                    success_ratio=extraction_summary.get("success_ratio"),
                    ocr_page_count=extraction_summary.get("ocr_page_count"),
                    fallback_page_count=extraction_summary.get("fallback_page_count"),
                    partial_extraction=extraction_summary.get("partial_extraction"),
                )

            document.status = DocumentStatus.CHUNKING

            await self._chunk_repository.delete_by_document(document.id)
            text_chunks, run_metadata = await self._chunking.split_document(parsed)
            await self._store_parsed_json(parsed_json_key, parsed, run_metadata=run_metadata)
            chunk_entities = [
                DocumentChunk(
                    project_id=document.project_id,
                    document_id=document.id,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    page_number=chunk.page_number,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
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
                selected_strategy=run_metadata.strategy_used.value,
                structure_score=run_metadata.structure_score,
                structure_signals=run_metadata.structure_signals,
                semantic_refinement_used=run_metadata.semantic_refinement_used,
                avg_token_count=run_metadata.avg_token_count,
                processing_time_ms=run_metadata.processing_time_ms,
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

    async def _store_parsed_json(
        self,
        key: str,
        parsed: ParsedDocument,
        *,
        run_metadata: ChunkingRunMetadata | None = None,
    ) -> None:
        payload_dict = parsed.to_dict()
        if run_metadata is not None:
            payload_dict["chunking_run"] = run_metadata.to_dict()
        payload = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")
        await self._storage.put(
            key,
            _byte_stream(payload),
            content_type="application/json; charset=utf-8",
            size_bytes=len(payload),
        )
