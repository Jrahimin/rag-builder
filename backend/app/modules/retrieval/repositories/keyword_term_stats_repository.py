"""Keyword term and collection statistics persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.keyword_term_stats import KeywordCollectionStats, KeywordTermStats
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


class KeywordTermStatsRepository(ProjectScopedRepository[KeywordTermStats]):
    """Document frequency stats per term."""

    model = KeywordTermStats

    def __init__(self, session: AsyncSession, project_id: uuid.UUID) -> None:
        super().__init__(session, project_id)

    async def delete_for_version(self, embedding_set_version: int) -> None:
        stmt = delete(self.model).where(
            self.model.project_id == self._project_id,
            self.model.embedding_set_version == embedding_set_version,
        )
        await self._session.execute(stmt)

    async def delete_by_document_terms(
        self,
        *,
        embedding_set_version: int,
        terms: set[str],
    ) -> None:
        if not terms:
            return
        stmt = delete(self.model).where(
            self.model.project_id == self._project_id,
            self.model.embedding_set_version == embedding_set_version,
            self.model.term.in_(terms),
        )
        await self._session.execute(stmt)

    async def map_document_frequencies(
        self,
        terms: list[str],
        *,
        embedding_set_version: int,
    ) -> dict[str, int]:
        if not terms:
            return {}
        stmt = select(self.model.term, self.model.document_frequency).where(
            self.model.project_id == self._project_id,
            self.model.embedding_set_version == embedding_set_version,
            self.model.term.in_(terms),
        )
        result = await self._session.execute(stmt)
        return {row.term: row.document_frequency for row in result.all()}

    async def increment_term(
        self,
        term: str,
        *,
        embedding_set_version: int,
        delta: int = 1,
    ) -> None:
        stmt = select(self.model).where(
            self.model.project_id == self._project_id,
            self.model.embedding_set_version == embedding_set_version,
            self.model.term == term,
        )
        result = await self._session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is None:
            self.add(
                KeywordTermStats(
                    project_id=self._project_id,
                    embedding_set_version=embedding_set_version,
                    term=term,
                    document_frequency=delta,
                )
            )
            return
        existing.document_frequency += delta


class KeywordCollectionStatsRepository(ProjectScopedRepository[KeywordCollectionStats]):
    """Collection-level BM25 statistics."""

    model = KeywordCollectionStats

    def __init__(self, session: AsyncSession, project_id: uuid.UUID) -> None:
        super().__init__(session, project_id)

    async def get_for_version(self, embedding_set_version: int) -> KeywordCollectionStats | None:
        stmt = select(self.model).where(
            self.model.project_id == self._project_id,
            self.model.embedding_set_version == embedding_set_version,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_stats(
        self,
        *,
        embedding_set_version: int,
        total_documents: int,
        total_chunks: int,
        avg_doc_length: float,
    ) -> KeywordCollectionStats:
        existing = await self.get_for_version(embedding_set_version)
        if existing is None:
            row = KeywordCollectionStats(
                project_id=self._project_id,
                embedding_set_version=embedding_set_version,
                total_documents=total_documents,
                total_chunks=total_chunks,
                avg_doc_length=avg_doc_length,
            )
            self.add(row)
            return row
        existing.total_documents = total_documents
        existing.total_chunks = total_chunks
        existing.avg_doc_length = avg_doc_length
        return existing

    async def delete_for_version(self, embedding_set_version: int) -> None:
        stmt = delete(self.model).where(
            self.model.project_id == self._project_id,
            self.model.embedding_set_version == embedding_set_version,
        )
        await self._session.execute(stmt)
