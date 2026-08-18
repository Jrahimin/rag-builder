"""Hydrate final candidate hits into stable RetrievalResult DTOs."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.retrieval.repositories.retrieval_chunk_repository import RetrievalChunkRepository
from app.modules.retrieval.repositories.retrieval_document_repository import (
    RetrievalDocumentRepository,
)
from app.modules.retrieval.retrievers.models import CandidateHit
from app.modules.retrieval.schemas.search import RetrievalResult


class ResultHydrator:
    """Single hydration point for chunk/document ORM rows."""

    def __init__(self, session: AsyncSession, project_id: uuid.UUID) -> None:
        self._project_id = project_id
        self._chunk_repository = RetrievalChunkRepository(session, project_id)
        self._document_repository = RetrievalDocumentRepository(session, project_id)

    async def hydrate(self, candidates: list[CandidateHit]) -> list[RetrievalResult]:
        if not candidates:
            return []

        chunk_ids = [candidate.chunk_id for candidate in candidates]
        chunks = await self._chunk_repository.map_by_ids(chunk_ids)
        documents = await self._document_repository.map_by_ids(
            {chunk.document_id for chunk in chunks.values()}
        )

        results: list[RetrievalResult] = []
        for candidate in candidates:
            chunk = chunks.get(candidate.chunk_id)
            if chunk is None:
                continue
            document = documents.get(chunk.document_id)
            if document is None:
                continue
            results.append(
                RetrievalResult(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    score=candidate.score,
                    semantic_score=candidate.semantic_score,
                    rank_score=candidate.rank_score,
                    rerank_relevance_score=candidate.rerank_relevance_score,
                    evidence_relevance_score=candidate.evidence_relevance_score,
                    evidence_score_method=candidate.evidence_score_method,
                    evidence_calibration_id=candidate.evidence_calibration_id,
                    passage_semantic_score=_optional_float(
                        candidate.metadata.get("passage_semantic_score")
                    ),
                    passage_char_start=_optional_int(
                        candidate.metadata.get("passage_char_start")
                    ),
                    passage_char_end=_optional_int(candidate.metadata.get("passage_char_end")),
                    passage_score_method=_optional_string(
                        candidate.metadata.get("passage_score_method")
                    ),
                    filename=document.filename,
                    page_number=chunk.page_number,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    metadata={
                        **chunk.chunk_metadata,
                        **_public_candidate_metadata(candidate.metadata),
                        "retrieval_source": candidate.source.value,
                        "processing_version": chunk.document_version,
                    },
                )
            )
        return results


_PRIVATE_CANDIDATE_METADATA_KEYS = frozenset({"translated_query"})


def _public_candidate_metadata(metadata: dict[str, object]) -> dict[str, object]:
    """Drop retrieval artifacts that must not appear on hydrated hits or citations."""
    return {
        key: value
        for key, value in metadata.items()
        if key not in _PRIVATE_CANDIDATE_METADATA_KEYS
    }


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None
