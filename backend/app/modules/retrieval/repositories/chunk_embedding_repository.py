"""Chunk embedding persistence — project- and document-scoped."""

from __future__ import annotations

import uuid

from sqlalchemy import delete

from app.models.chunk_embedding import ChunkEmbedding
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


class ChunkEmbeddingRepository(ProjectScopedRepository[ChunkEmbedding]):
    """Async access to chunk embeddings within a Project."""

    model = ChunkEmbedding

    async def list_by_document(
        self,
        document_id: uuid.UUID,
        *,
        embedding_set_version: int,
        provider: str,
        model: str,
    ) -> list[ChunkEmbedding]:
        stmt = (
            self._scoped()
            .where(self.model.document_id == document_id)
            .where(self.model.embedding_set_version == embedding_set_version)
            .where(self.model.provider == provider)
            .where(self.model.model == model)
            .order_by(self.model.chunk_id)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def delete_by_document(self, document_id: uuid.UUID) -> None:
        stmt = (
            delete(self.model)
            .where(self.model.project_id == self._project_id)
            .where(self.model.document_id == document_id)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def delete_for_document_version(
        self,
        document_id: uuid.UUID,
        *,
        embedding_set_version: int,
        provider: str,
        model: str,
    ) -> None:
        stmt = (
            delete(self.model)
            .where(self.model.project_id == self._project_id)
            .where(self.model.document_id == document_id)
            .where(self.model.embedding_set_version == embedding_set_version)
            .where(self.model.provider == provider)
            .where(self.model.model == model)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    def bulk_add(self, embeddings: list[ChunkEmbedding]) -> None:
        for embedding in embeddings:
            self.add(embedding)

    async def get_by_chunk_ids(
        self,
        chunk_ids: list[uuid.UUID],
        *,
        embedding_set_version: int,
        provider: str,
        model: str,
    ) -> dict[uuid.UUID, ChunkEmbedding]:
        if not chunk_ids:
            return {}
        stmt = (
            self._scoped()
            .where(self.model.chunk_id.in_(chunk_ids))
            .where(self.model.embedding_set_version == embedding_set_version)
            .where(self.model.provider == provider)
            .where(self.model.model == model)
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        return {row.chunk_id: row for row in rows}
