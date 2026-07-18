"""Native pgvector persistence and semantic search — always Project-scoped."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, literal, select, text

from app.models.chunk_embedding import ChunkEmbedding
from app.models.document_chunk import DocumentChunk
from app.modules.retrieval.retrievers.models import CandidateHit, CandidateSource
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
            if embedding.project_id != self._project_id:
                msg = "Embedding project_id does not match repository scope"
                raise ValueError(msg)
        self._session.add_all(embeddings)

    async def search_cosine(
        self,
        *,
        query_vector: list[float],
        top_k: int,
        index_build_id: uuid.UUID | None = None,
        embedding_set_version: int,
        provider: str,
        model: str,
        document_id: uuid.UUID | None = None,
        metadata_filter: dict[str, str] | None = None,
        score_threshold: float | None = None,
        hnsw_ef_search: int = 100,
    ) -> list[CandidateHit]:
        """Return nearest native-vector candidates with all filters inside SQL."""
        await self._session.execute(
            text("SELECT set_config('hnsw.ef_search', :value, true)"),
            {"value": str(hnsw_ef_search)},
        )

        distance = self.model.embedding.cosine_distance(query_vector)
        score = (literal(1.0) - distance).label("score")
        stmt = (
            select(self.model.chunk_id, score, DocumentChunk.chunk_metadata)
            .join(
                DocumentChunk,
                (DocumentChunk.id == self.model.chunk_id)
                & (DocumentChunk.project_id == self.model.project_id),
            )
            .where(self.model.project_id == self._project_id)
            .where(DocumentChunk.project_id == self._project_id)
            .where(self.model.embedding_set_version == embedding_set_version)
            .where(self.model.provider == provider)
            .where(self.model.model == model)
        )
        if index_build_id is not None:
            stmt = stmt.where(self.model.index_build_id == index_build_id)
        if document_id is not None:
            stmt = stmt.where(self.model.document_id == document_id)
        for key, value in (metadata_filter or {}).items():
            stmt = stmt.where(DocumentChunk.chunk_metadata[key].astext == value)
        if score_threshold is not None:
            stmt = stmt.where(distance <= 1.0 - score_threshold)
        stmt = stmt.order_by(distance, self.model.chunk_id).limit(top_k)

        result = await self._session.execute(stmt)
        return [
            CandidateHit(
                chunk_id=row.chunk_id,
                score=float(row.score),
                source=CandidateSource.SEMANTIC,
                metadata=dict(_metadata_dict(row.chunk_metadata)),
            )
            for row in result
        ]

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


def _metadata_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
