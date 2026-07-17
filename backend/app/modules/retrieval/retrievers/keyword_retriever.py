"""BM25 keyword retriever — returns candidate hits only."""

from __future__ import annotations

import time
import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.retrieval.keyword.bm25 import bm25_score
from app.modules.retrieval.keyword.tokenizer import tokenize
from app.modules.retrieval.repositories.chunk_keyword_index_repository import (
    ChunkKeywordIndexRepository,
)
from app.modules.retrieval.repositories.keyword_term_stats_repository import (
    KeywordCollectionStatsRepository,
    KeywordTermStatsRepository,
)
from app.modules.retrieval.retrievers.base_retriever import BaseRetriever
from app.modules.retrieval.retrievers.models import CandidateHit, CandidateSource, RetrievalContext

logger = structlog.get_logger(__name__)


class KeywordRetriever(BaseRetriever):
    """BM25 keyword search over PostgreSQL keyword index rows."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        *,
        fts_regconfig: str = "simple",
    ) -> None:
        self._keyword_repository = ChunkKeywordIndexRepository(
            session,
            project_id,
            fts_regconfig=fts_regconfig,
        )
        self._term_stats_repository = KeywordTermStatsRepository(session, project_id)
        self._collection_stats_repository = KeywordCollectionStatsRepository(session, project_id)

    async def retrieve(self, context: RetrievalContext) -> list[CandidateHit]:
        started = time.perf_counter()
        query_terms = tokenize(context.query, for_query=True)
        if not query_terms:
            return []

        metadata_filter = context.sanitized_metadata_filter()
        rows = await self._keyword_repository.search_candidates(
            query=context.query,
            embedding_set_version=context.embedding_set_version,
            top_k=context.keyword_candidate_top_k,
            document_id=context.filters.document_id,
            metadata_filter=metadata_filter,
        )

        if context.min_ocr_confidence is not None:
            threshold = context.min_ocr_confidence
            rows = [row for row in rows if _passes_ocr_threshold(row.metadata_snapshot, threshold)]

        unique_terms = list(dict.fromkeys(query_terms))
        document_frequencies = await self._term_stats_repository.map_document_frequencies(
            unique_terms,
            embedding_set_version=context.embedding_set_version,
        )
        collection_stats = await self._collection_stats_repository.get_for_version(
            context.embedding_set_version
        )
        total_documents = (
            collection_stats.total_documents if collection_stats else max(len(rows), 1)
        )
        avg_doc_length = collection_stats.avg_doc_length if collection_stats else 1.0

        scored: list[tuple[uuid.UUID, float, dict]] = []
        for row in rows:
            score = bm25_score(
                unique_terms,
                term_frequencies=dict(row.term_frequencies),
                doc_length=row.token_count,
                avg_doc_length=avg_doc_length,
                total_documents=total_documents,
                document_frequencies=document_frequencies,
            )
            if score <= 0:
                continue
            scored.append((row.chunk_id, score, dict(row.metadata_snapshot)))

        scored.sort(key=lambda item: (-item[1], str(item[0])))
        candidates = [
            CandidateHit(
                chunk_id=chunk_id,
                score=score,
                source=CandidateSource.KEYWORD,
                metadata=metadata,
            )
            for chunk_id, score, metadata in scored[: context.keyword_candidate_top_k]
        ]

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "keyword_retrieve_complete",
            project_id=str(context.project_id),
            duration_ms=elapsed_ms,
            candidate_count=len(candidates),
            top_k=context.keyword_candidate_top_k,
        )
        return candidates


def _passes_ocr_threshold(metadata: dict, threshold: float) -> bool:
    raw = metadata.get("ocr_confidence")
    if raw is None:
        return True
    try:
        return float(raw) >= threshold
    except (TypeError, ValueError):
        return True
