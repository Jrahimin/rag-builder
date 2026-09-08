"""Load candidate text for reranking without full result hydration."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.retrieval.repositories.chunk_keyword_index_repository import (
    ChunkKeywordIndexRepository,
)
from app.modules.retrieval.repositories.retrieval_chunk_repository import RetrievalChunkRepository
from app.platform.providers.request_work import RequestWork, observe_stage


class CandidateContentLoader:
    """Fetch chunk text for the rerank window only."""

    def __init__(
        self, session: AsyncSession, project_id: uuid.UUID, *, work: RequestWork | None = None
    ) -> None:
        self._work = work if isinstance(work, RequestWork) else None
        self._project_id = project_id
        self._keyword_repository = ChunkKeywordIndexRepository(session, project_id)
        self._chunk_repository = RetrievalChunkRepository(session, project_id)

    @observe_stage("content_loading")
    async def load_texts(self, chunk_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not chunk_ids:
            return {}
        if self._work is None:
            return await self._load_uncached(chunk_ids)
        cached = {
            chunk_id: self._work.content.get((self._project_id, chunk_id)) for chunk_id in chunk_ids
        }
        found = {key: value for key, value in cached.items() if isinstance(value, str)}
        self._work.counts["content_cache_hits"] += len(found)
        missing = [chunk_id for chunk_id in chunk_ids if chunk_id not in found]
        loaded = await self._load_uncached(missing) if missing else {}
        self._work.content.update({(self._project_id, key): value for key, value in loaded.items()})
        return {**found, **loaded}

    async def _load_uncached(self, chunk_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
        from_keyword = await self._keyword_repository.map_content_by_ids(chunk_ids)
        missing = [chunk_id for chunk_id in chunk_ids if chunk_id not in from_keyword]
        if not missing:
            return from_keyword
        chunks = await self._chunk_repository.map_by_ids(missing)
        from_chunks = {chunk_id: chunk.content for chunk_id, chunk in chunks.items()}
        return {**from_keyword, **from_chunks}
