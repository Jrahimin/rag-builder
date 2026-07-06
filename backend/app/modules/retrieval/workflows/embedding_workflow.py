"""Embedding workflow — generate and persist chunk vectors."""

from __future__ import annotations

import time
import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk_embedding import EMBEDDING_SCHEMA_VERSION, ChunkEmbedding
from app.models.document import Document, DocumentStatus
from app.modules.retrieval.repositories.chunk_embedding_repository import ChunkEmbeddingRepository
from app.modules.retrieval.repositories.retrieval_chunk_repository import RetrievalChunkRepository
from app.modules.retrieval.repositories.retrieval_document_repository import (
    RetrievalDocumentRepository,
)
from app.modules.retrieval.workflows.stage_runner import StageFailure, run_document_stage
from app.platform.domain.content_hash import content_hash
from app.platform.persistence.vector_codec import pack_vector
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider

logger = structlog.get_logger(__name__)


class EmbeddingWorkflow:
    """Embed all chunks for a document and persist vectors in PostgreSQL."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        embedder: BaseEmbeddingProvider,
        *,
        embedding_set_version: int,
        batch_size: int,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._embedder = embedder
        self._embedding_set_version = embedding_set_version
        self._batch_size = batch_size
        self._document_repository = RetrievalDocumentRepository(session, project_id)
        self._chunk_repository = RetrievalChunkRepository(session, project_id)
        self._embedding_repository = ChunkEmbeddingRepository(session, project_id)

    async def run(self, document_id: uuid.UUID) -> Document | None:
        return await run_document_stage(
            session=self._session,
            repository=self._document_repository,
            project_id=self._project_id,
            document_id=document_id,
            stage="embedding",
            allowed_statuses={DocumentStatus.EMBEDDING, DocumentStatus.CHUNKED},
            failure_message="Embedding failed.",
            work=self._embed,
        )

    async def _embed(self, document: Document) -> None:
        started = time.perf_counter()
        chunks = await self._chunk_repository.list_by_document(document.id)
        if not chunks:
            raise StageFailure("No chunks available for embedding.")

        await self._embedding_repository.delete_for_document_version(
            document.id,
            embedding_set_version=self._embedding_set_version,
            provider=self._embedder.provider_name,
            model=self._embedder.model_name,
        )

        total = 0
        for offset in range(0, len(chunks), self._batch_size):
            batch = chunks[offset : offset + self._batch_size]
            texts = [chunk.content for chunk in batch]
            result = await self._embedder.embed_texts(texts)
            entities = [
                ChunkEmbedding(
                    project_id=document.project_id,
                    document_id=document.id,
                    chunk_id=chunk.id,
                    embedding_set_version=self._embedding_set_version,
                    document_version=document.version,
                    provider=result.provider,
                    model=result.model,
                    dimensions=result.dimensions,
                    provider_version=result.provider_version,
                    input_content_hash=content_hash(chunk.content),
                    embedding_schema_version=EMBEDDING_SCHEMA_VERSION,
                    vector=pack_vector(vector),
                )
                for chunk, vector in zip(batch, result.vectors, strict=True)
            ]
            self._embedding_repository.bulk_add(entities)
            await self._embedding_repository.flush()
            total += len(batch)

        document.status = DocumentStatus.EMBEDDED
        document.error_message = None
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "embedding_complete",
            project_id=str(self._project_id),
            document_id=str(document.id),
            duration_ms=elapsed_ms,
            batch_size=self._batch_size,
            chunk_count=total,
            provider=self._embedder.provider_name,
            model=self._embedder.model_name,
            embedding_set_version=self._embedding_set_version,
        )
