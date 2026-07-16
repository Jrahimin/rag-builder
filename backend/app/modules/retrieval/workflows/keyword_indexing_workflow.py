"""Keyword index workflow — build BM25 rows for a document."""

from __future__ import annotations

import time
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk_keyword_index import ChunkKeywordIndex
from app.models.document import Document
from app.modules.retrieval.keyword.tokenizer import tokenize
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

    async def index_document(self, document: Document) -> int:
        started = time.perf_counter()
        chunks = await self._chunk_repository.list_by_document(document.id)
        await self._keyword_repository.delete_by_document(
            document.id,
            embedding_set_version=self._embedding_set_version,
        )

        indexed = 0
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
            indexed += 1

        await refresh_keyword_statistics(
            self._session,
            self._project_id,
            self._embedding_set_version,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "keyword_indexing_complete",
            project_id=str(self._project_id),
            document_id=str(document.id),
            duration_ms=elapsed_ms,
            chunk_count=indexed,
            total_tokens=total_tokens,
        )
        return indexed


async def refresh_keyword_statistics(
    session: AsyncSession,
    project_id: uuid.UUID,
    embedding_set_version: int,
) -> None:
    """Rebuild exact BM25 statistics after an index mutation.

    Rebuilding from the authoritative chunk rows keeps reruns and deletions
    idempotent. The operation stays inside the caller's transaction.
    """
    await session.flush()
    result = await session.execute(
        select(
            ChunkKeywordIndex.document_id,
            ChunkKeywordIndex.term_frequencies,
            ChunkKeywordIndex.token_count,
        ).where(
            ChunkKeywordIndex.project_id == project_id,
            ChunkKeywordIndex.embedding_set_version == embedding_set_version,
        )
    )
    rows = result.all()
    documents_by_term: dict[str, set[uuid.UUID]] = {}
    document_ids: set[uuid.UUID] = set()
    total_tokens = 0
    for row in rows:
        document_ids.add(row.document_id)
        total_tokens += row.token_count
        for term in row.term_frequencies:
            documents_by_term.setdefault(term, set()).add(row.document_id)

    term_repository = KeywordTermStatsRepository(session, project_id)
    await term_repository.delete_for_version(embedding_set_version)
    for term, document_ids_for_term in documents_by_term.items():
        await term_repository.increment_term(
            term,
            embedding_set_version=embedding_set_version,
            delta=len(document_ids_for_term),
        )

    collection_repository = KeywordCollectionStatsRepository(session, project_id)
    await collection_repository.upsert_stats(
        embedding_set_version=embedding_set_version,
        total_documents=len(document_ids),
        total_chunks=len(rows),
        avg_doc_length=(total_tokens / len(rows)) if rows else 1.0,
    )
