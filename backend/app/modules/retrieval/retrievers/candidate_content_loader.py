"""Load candidate text for reranking without full result hydration."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.retrieval.repositories.chunk_keyword_index_repository import (
    ChunkKeywordIndexRepository,
)
from app.modules.retrieval.repositories.retrieval_chunk_repository import RetrievalChunkRepository


class CandidateContentLoader:
    """Fetch chunk text for the rerank window only."""

    def __init__(self, session: AsyncSession, project_id: uuid.UUID) -> None:
        self._keyword_repository = ChunkKeywordIndexRepository(session, project_id)
        self._chunk_repository = RetrievalChunkRepository(session, project_id)

    async def load_texts(self, chunk_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not chunk_ids:
            return {}
        from_keyword = await self._keyword_repository.map_content_by_ids(chunk_ids)
        missing = [chunk_id for chunk_id in chunk_ids if chunk_id not in from_keyword]
        if not missing:
            return from_keyword
        chunks = await self._chunk_repository.map_by_ids(missing)
        from_chunks = {chunk_id: chunk.content for chunk_id, chunk in chunks.items()}
        return {**from_keyword, **from_chunks}
