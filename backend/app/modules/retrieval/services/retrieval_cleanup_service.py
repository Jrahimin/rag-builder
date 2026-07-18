"""Retrieval artifact cleanup — transactional delete cascade."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.retrieval.repositories.chunk_embedding_repository import ChunkEmbeddingRepository
from app.modules.retrieval.repositories.chunk_keyword_index_repository import (
    ChunkKeywordIndexRepository,
)


class RetrievalCleanupService:
    """Remove native vector and keyword rows in the caller's transaction."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._embedding_repository = ChunkEmbeddingRepository(session, project_id)
        self._keyword_repository = ChunkKeywordIndexRepository(session, project_id)

    async def delete_embeddings_for_document(self, document_id: uuid.UUID) -> None:
        await self._embedding_repository.delete_by_document(document_id)

    async def delete_keyword_index_for_document(self, document_id: uuid.UUID) -> None:
        await self._keyword_repository.delete_by_document_all_versions(document_id)

    async def on_document_delete(self, document_id: uuid.UUID) -> None:
        """Delete every retrieval row before the document transaction commits."""
        await self.delete_embeddings_for_document(document_id)
        await self.delete_keyword_index_for_document(document_id)
