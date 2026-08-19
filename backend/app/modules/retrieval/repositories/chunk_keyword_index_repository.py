"""Chunk keyword index persistence."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk_keyword_index import ChunkKeywordIndex
from app.modules.retrieval.keyword.fts import (
    keyword_candidate_predicate,
    plain_query,
    to_search_vector,
)
from app.modules.retrieval.keyword.tokenizer import (
    normalize_for_indexing,
    normalize_for_query,
    term_frequencies,
    tokenize,
)
from app.modules.retrieval.language_scope import LanguageScope, language_scope_predicate
from app.modules.retrieval.source_policy import (
    SOURCE_METADATA_COLUMNS,
    SourceMetadataScope,
    source_metadata_from_row,
)
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


@dataclass(frozen=True, slots=True)
class KeywordCandidateRow:
    chunk_id: uuid.UUID
    token_count: int
    term_frequencies: dict[str, Any]
    metadata_snapshot: dict[str, Any]
    source_metadata: dict[str, Any]


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
            search_vector=to_search_vector(self._fts_regconfig, normalized),
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
        source_scope: SourceMetadataScope | None = None,
        language_scope: LanguageScope | None = None,
    ) -> list[KeywordCandidateRow]:
        """FTS or tokenizer-key overlap — BM25 scoring happens in the retriever."""
        normalized_query = normalize_for_query(query)
        query_terms = tokenize(query, for_query=True)
        ts_query = plain_query(self._fts_regconfig, normalized_query)
        candidate_match = keyword_candidate_predicate(
            self.model.search_vector,
            self.model.term_frequencies,
            regconfig=self._fts_regconfig,
            query=normalized_query,
            query_terms=query_terms,
        )
        source_columns = (
            [source_scope.selectable.c[name] for name in SOURCE_METADATA_COLUMNS]
            if source_scope is not None and source_scope.selectable is not None
            else []
        )
        stmt = (
            select(self.model, *source_columns)
            .where(
                self.model.project_id == self._project_id,
                self.model.embedding_set_version == embedding_set_version,
                candidate_match,
            )
            .order_by(
                func.ts_rank_cd(self.model.search_vector, ts_query).desc(),
                self.model.chunk_id,
            )
            .limit(top_k)
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
        if metadata_filter:
            for key, value in metadata_filter.items():
                stmt = stmt.where(self.model.metadata_snapshot[key].astext == value)
        language_predicate = language_scope_predicate(
            self.model.metadata_snapshot["chunk_language"].astext,
            language_scope,
        )
        if language_predicate is not None:
            stmt = stmt.where(language_predicate)
        result = await self._session.execute(stmt)
        return [
            KeywordCandidateRow(
                chunk_id=row[0].chunk_id,
                token_count=row[0].token_count,
                term_frequencies=dict(row[0].term_frequencies),
                metadata_snapshot=dict(row[0].metadata_snapshot),
                source_metadata=source_metadata_from_row(row),
            )
            for row in result.all()
        ]

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
                SET search_vector = to_tsvector(CAST(:regconfig AS regconfig), content_normalized)
                WHERE project_id = :project_id
                  AND search_vector IS NULL
                """
            ),
            {
                "regconfig": self._fts_regconfig,
                "project_id": self._project_id,
            },
        )
