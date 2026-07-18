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
                    filename=document.filename,
                    page_number=chunk.page_number,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    metadata={
                        **chunk.chunk_metadata,
                        **candidate.metadata,
                        "retrieval_source": candidate.source.value,
                    },
                )
            )
        return results
