"""Retrieval artifact cleanup — delete cascade for document removal."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.retrieval.repositories.chunk_embedding_repository import ChunkEmbeddingRepository
from app.platform.providers.contracts.vector_store import BaseVectorStoreProvider

logger = structlog.get_logger(__name__)


class RetrievalCleanupService:
    """Remove retrieval artifacts (PG embeddings + vector points) for a document.

    Intentionally lightweight: needs only a session and the vector store, so the
    composition layer can wire the knowledge delete cascade without building the
    full embedding/indexing stack.
    """

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        vector_store: BaseVectorStoreProvider,
    ) -> None:
        self._project_id = project_id
        self._vector_store = vector_store
        self._embedding_repository = ChunkEmbeddingRepository(session, project_id)

    async def delete_embeddings_for_document(self, document_id: uuid.UUID) -> None:
        await self._embedding_repository.delete_by_document(document_id)

    async def purge_document_vectors(self, document_id: uuid.UUID) -> None:
        """Best-effort vector store purge — failures are logged, not raised."""
        try:
            await self._vector_store.delete_by_document(
                project_id=self._project_id,
                document_id=document_id,
            )
        except Exception as exc:
            logger.warning(
                "vector_purge_failed",
                project_id=str(self._project_id),
                document_id=str(document_id),
                error=str(exc),
            )

    async def on_document_delete(self, document_id: uuid.UUID) -> None:
        """Full delete cascade: PG embeddings first, then vector points."""
        await self.delete_embeddings_for_document(document_id)
        await self.purge_document_vectors(document_id)
