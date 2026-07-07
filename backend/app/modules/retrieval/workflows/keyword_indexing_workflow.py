"""Keyword index workflow — build BM25 rows for a document."""

from __future__ import annotations

import time
import uuid

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk_keyword_index import ChunkKeywordIndex
from app.models.document import Document
from app.modules.retrieval.keyword.tokenizer import term_frequencies, tokenize
from app.modules.retrieval.repositories.chunk_keyword_index_repository import (
    ChunkKeywordIndexRepository,
)
from app.modules.retrieval.repositories.keyword_term_stats_repository import (
    KeywordCollectionStatsRepository,
    KeywordTermStatsRepository,
)
from app.modules.retrieval.repositories.retrieval_chunk_repository import RetrievalChunkRepository

logger = structlog.get_logger(__name__)


class KeywordIndexingWorkflow:
    """Build PostgreSQL keyword index rows for a document."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        *,
        embedding_set_version: int,
        filterable_metadata_keys: list[str],
        fts_regconfig: str = "simple",
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._embedding_set_version = embedding_set_version
        self._filterable_metadata_keys = filterable_metadata_keys
        self._fts_regconfig = fts_regconfig
        self._chunk_repository = RetrievalChunkRepository(session, project_id)
        self._keyword_repository = ChunkKeywordIndexRepository(
            session,
            project_id,
            fts_regconfig=fts_regconfig,
        )
        self._term_stats_repository = KeywordTermStatsRepository(session, project_id)
        self._collection_stats_repository = KeywordCollectionStatsRepository(session, project_id)

    async def index_document(self, document: Document) -> int:
        started = time.perf_counter()
        chunks = await self._chunk_repository.list_by_document(document.id)
        await self._keyword_repository.delete_by_document(
            document.id,
            embedding_set_version=self._embedding_set_version,
        )

        indexed = 0
        document_terms: set[str] = set()
        total_tokens = 0
        for chunk in chunks:
            metadata_snapshot = {
                key: str(chunk.chunk_metadata[key])
                for key in self._filterable_metadata_keys
                if key in chunk.chunk_metadata
            }
            await self._keyword_repository.upsert_chunk_row(
                document_id=document.id,
                chunk_id=chunk.id,
                embedding_set_version=self._embedding_set_version,
                document_version=document.version,
                content=chunk.content,
                metadata_snapshot=metadata_snapshot,
            )
            tokens = tokenize(chunk.content)
            total_tokens += len(tokens)
            document_terms.update(term_frequencies(tokens).keys())
            indexed += 1

        for term in document_terms:
            await self._term_stats_repository.increment_term(
                term,
                embedding_set_version=self._embedding_set_version,
            )

        await self._refresh_collection_stats()
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "keyword_indexing_complete",
            project_id=str(self._project_id),
            document_id=str(document.id),
            duration_ms=elapsed_ms,
            chunk_count=indexed,
            unique_terms=len(document_terms),
            total_tokens=total_tokens,
        )
        return indexed

    async def _refresh_collection_stats(self) -> None:
        stmt = select(
            func.count(ChunkKeywordIndex.id),
            func.avg(ChunkKeywordIndex.token_count),
            func.count(func.distinct(ChunkKeywordIndex.document_id)),
        ).where(
            ChunkKeywordIndex.project_id == self._project_id,
            ChunkKeywordIndex.embedding_set_version == self._embedding_set_version,
        )
        result = await self._session.execute(stmt)
        total_chunks, avg_doc_length, total_documents = result.one()
        await self._collection_stats_repository.upsert_stats(
            embedding_set_version=self._embedding_set_version,
            total_documents=int(total_documents or 0),
            total_chunks=int(total_chunks or 0),
            avg_doc_length=float(avg_doc_length or 0.0) or 1.0,
        )
