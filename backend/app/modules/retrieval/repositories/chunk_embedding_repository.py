"""Native pgvector persistence and semantic search — always Project-scoped."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, literal, select, text

from app.models.chunk_embedding import ChunkEmbedding
from app.models.chunk_keyword_index import ChunkKeywordIndex
from app.modules.retrieval.language_scope import LanguageScope, language_scope_predicate
from app.modules.retrieval.retrievers.models import CandidateHit, CandidateSource
from app.modules.retrieval.source_policy import (
    SOURCE_METADATA_COLUMNS,
    SourceMetadataScope,
    source_metadata_from_row,
)
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
        source_scope: SourceMetadataScope | None = None,
        language_scope: LanguageScope | None = None,
    ) -> list[CandidateHit]:
        """Return nearest native-vector candidates with all filters inside SQL."""
        await self._session.execute(
            text("SELECT set_config('hnsw.ef_search', :value, true)"),
            {"value": str(hnsw_ef_search)},
        )

        distance = self.model.embedding.cosine_distance(query_vector)
        score = (literal(1.0) - distance).label("score")
        source_columns = (
            [source_scope.selectable.c[name] for name in SOURCE_METADATA_COLUMNS]
            if source_scope is not None and source_scope.selectable is not None
            else []
        )
        stmt = (
            select(
                self.model.chunk_id,
                score,
                ChunkKeywordIndex.metadata_snapshot,
                *source_columns,
            )
            .join(
                ChunkKeywordIndex,
                (ChunkKeywordIndex.chunk_id == self.model.chunk_id)
                & (ChunkKeywordIndex.project_id == self.model.project_id)
                & (ChunkKeywordIndex.index_build_id == self.model.index_build_id)
                & (ChunkKeywordIndex.embedding_set_version == self.model.embedding_set_version),
            )
            .where(self.model.project_id == self._project_id)
            .where(ChunkKeywordIndex.project_id == self._project_id)
            .where(self.model.embedding_set_version == embedding_set_version)
            .where(self.model.provider == provider)
            .where(self.model.model == model)
        )
        if source_scope is not None and source_scope.selectable is not None:
            stmt = stmt.join(
                source_scope.selectable,
                source_scope.selectable.c.source_document_id == self.model.document_id,
            )
        if index_build_id is not None:
            stmt = stmt.where(self.model.index_build_id == index_build_id)
        if document_id is not None:
            stmt = stmt.where(self.model.document_id == document_id)
        for key, value in (metadata_filter or {}).items():
            stmt = stmt.where(ChunkKeywordIndex.metadata_snapshot[key].astext == value)
        language_predicate = language_scope_predicate(
            ChunkKeywordIndex.metadata_snapshot["chunk_language"].astext,
            language_scope,
        )
        if language_predicate is not None:
            stmt = stmt.where(language_predicate)
        if score_threshold is not None:
            stmt = stmt.where(distance <= 1.0 - score_threshold)
        stmt = stmt.order_by(distance, self.model.chunk_id).limit(top_k)

        result = await self._session.execute(stmt)
        return [
            CandidateHit(
                chunk_id=row.chunk_id,
                score=float(row.score),
                source=CandidateSource.SEMANTIC,
                semantic_score=float(row.score),
                metadata={
                    **_metadata_dict(row.metadata_snapshot),
                    **source_metadata_from_row(row),
                },
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

    async def score_chunk_ids(
        self,
        chunk_ids: list[uuid.UUID],
        *,
        query_vector: list[float],
        index_build_id: uuid.UUID,
        embedding_set_version: int,
        provider: str,
        model: str,
    ) -> dict[uuid.UUID, float]:
        """Return calibrated cosine similarities for a bounded candidate set."""
        if not chunk_ids:
            return {}
        distance = self.model.embedding.cosine_distance(query_vector)
        score = (literal(1.0) - distance).label("score")
        stmt = (
            select(self.model.chunk_id, score)
            .where(self.model.project_id == self._project_id)
            .where(self.model.chunk_id.in_(chunk_ids))
            .where(self.model.index_build_id == index_build_id)
            .where(self.model.embedding_set_version == embedding_set_version)
            .where(self.model.provider == provider)
            .where(self.model.model == model)
        )
        result = await self._session.execute(stmt)
        return {row.chunk_id: float(row.score) for row in result}

    async def list_distinct_identities(
        self, index_build_id: uuid.UUID
    ) -> list[tuple[int, str, str, int]]:
        """Return unique (esv, provider, model, dimensions) tuples for one build."""
        stmt = (
            select(
                self.model.embedding_set_version,
                self.model.provider,
                self.model.model,
                self.model.dimensions,
            )
            .where(self.model.project_id == self._project_id)
            .where(self.model.index_build_id == index_build_id)
            .distinct()
            .order_by(
                self.model.embedding_set_version,
                self.model.provider,
                self.model.model,
                self.model.dimensions,
            )
        )
        result = await self._session.execute(stmt)
        return [
            (
                int(row.embedding_set_version),
                str(row.provider),
                str(row.model),
                int(row.dimensions),
            )
            for row in result.all()
        ]


def _metadata_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
