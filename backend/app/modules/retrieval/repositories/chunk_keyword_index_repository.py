"""Chunk keyword index persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk_keyword_index import ChunkKeywordIndex
from app.modules.retrieval.keyword.tokenizer import (
    normalize_for_indexing,
    normalize_for_query,
    term_frequencies,
    tokenize,
)
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


class ChunkKeywordIndexRepository(ProjectScopedRepository[ChunkKeywordIndex]):
    """Project-scoped keyword index rows."""

    model = ChunkKeywordIndex

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        *,
        fts_regconfig: str = "simple",
    ) -> None:
        super().__init__(session, project_id)
        self._fts_regconfig = fts_regconfig

    async def delete_by_document(
        self,
        document_id: uuid.UUID,
        *,
        embedding_set_version: int,
    ) -> None:
        stmt = delete(self.model).where(
            self.model.project_id == self._project_id,
            self.model.document_id == document_id,
            self.model.embedding_set_version == embedding_set_version,
        )
        await self._session.execute(stmt)

    async def delete_by_document_all_versions(self, document_id: uuid.UUID) -> None:
        stmt = delete(self.model).where(
            self.model.project_id == self._project_id,
            self.model.document_id == document_id,
        )
        await self._session.execute(stmt)

    async def list_versions_for_document(self, document_id: uuid.UUID) -> set[int]:
        stmt = select(self.model.embedding_set_version).where(
            self.model.project_id == self._project_id,
            self.model.document_id == document_id,
        )
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def upsert_chunk_row(
        self,
        *,
        document_id: uuid.UUID,
        chunk_id: uuid.UUID,
        embedding_set_version: int,
        document_version: int,
        content: str,
        metadata_snapshot: dict[str, str],
    ) -> ChunkKeywordIndex:
        normalized = normalize_for_indexing(content)
        tokens = tokenize(content)
        frequencies = term_frequencies(tokens)
        row = ChunkKeywordIndex(
            project_id=self._project_id,
            document_id=document_id,
            chunk_id=chunk_id,
            embedding_set_version=embedding_set_version,
            document_version=document_version,
            content_normalized=normalized,
            token_count=len(tokens),
            term_frequencies=frequencies,
            metadata_snapshot=metadata_snapshot,
            search_vector=func.to_tsvector(self._fts_regconfig, normalized),
        )
        self.add(row)
        return row

    async def search_candidates(
        self,
        *,
        query: str,
        index_build_id: uuid.UUID | None = None,
        embedding_set_version: int,
        top_k: int,
        document_id: uuid.UUID | None = None,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[ChunkKeywordIndex]:
        """FTS candidate narrowing — BM25 scoring happens in the retriever."""
        normalized_query = normalize_for_query(query)
        ts_query = func.plainto_tsquery(self._fts_regconfig, normalized_query)
        stmt = (
            select(self.model)
            .where(
                self.model.project_id == self._project_id,
                self.model.embedding_set_version == embedding_set_version,
                self.model.search_vector.op("@@")(ts_query),
            )
            .order_by(func.ts_rank_cd(self.model.search_vector, ts_query).desc())
            .limit(top_k)
        )
        if index_build_id is not None:
            stmt = stmt.where(self.model.index_build_id == index_build_id)
        if document_id is not None:
            stmt = stmt.where(self.model.document_id == document_id)
        if metadata_filter:
            for key, value in metadata_filter.items():
                stmt = stmt.where(self.model.metadata_snapshot[key].astext == value)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def map_content_by_ids(
        self, chunk_ids: list[uuid.UUID], *, index_build_id: uuid.UUID | None = None
    ) -> dict[uuid.UUID, str]:
        """Load normalized content for reranking without full hydration."""
        if not chunk_ids:
            return {}
        stmt = select(self.model.chunk_id, self.model.content_normalized).where(
            self.model.project_id == self._project_id,
            self.model.chunk_id.in_(chunk_ids),
        )
        if index_build_id is not None:
            stmt = stmt.where(self.model.index_build_id == index_build_id)
        result = await self._session.execute(stmt)
        return {row.chunk_id: row.content_normalized for row in result.all()}

    async def refresh_search_vectors(self) -> None:
        """Backfill search_vector from content_normalized if needed."""
        await self._session.execute(
            text(
                """
                UPDATE chunk_keyword_index
                SET search_vector = to_tsvector(:regconfig, content_normalized)
                WHERE project_id = :project_id
                  AND search_vector IS NULL
                """
            ),
            {
                "regconfig": self._fts_regconfig,
                "project_id": self._project_id,
            },
        )
