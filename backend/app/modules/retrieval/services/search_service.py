"""HTTP-facing search orchestration."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import RetrievalConfig
from app.modules.retrieval.retrievers.semantic_retriever import SemanticRetriever
from app.modules.retrieval.schemas.search import SearchRequest, SearchResponse
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.contracts.vector_store import BaseVectorStoreProvider

type EnsureProjectFn = Callable[[], Awaitable[None]]


class SearchService:
    """Project-scoped semantic search entry point."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        embedder: BaseEmbeddingProvider,
        vector_store: BaseVectorStoreProvider,
        retrieval_config: RetrievalConfig,
        *,
        ensure_project: EnsureProjectFn,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._embedder = embedder
        self._vector_store = vector_store
        self._config = retrieval_config
        self._ensure_project = ensure_project

    async def search(self, request: SearchRequest) -> SearchResponse:
        await self._ensure_project()
        retriever = SemanticRetriever(
            session=self._session,
            project_id=self._project_id,
            embedder=self._embedder,
            vector_store=self._vector_store,
            default_top_k=self._config.default_top_k,
            score_threshold=self._config.score_threshold,
            filterable_metadata_keys=self._config.filterable_metadata_keys,
            embedding_set_version=self._config.embedding_set_version,
        )
        top_k = request.top_k or self._config.default_top_k
        results = await retriever.search(
            query=request.query,
            top_k=top_k,
            document_id=request.document_id,
            metadata_filter=request.metadata_filter,
        )
        return SearchResponse(results=results, query=request.query, top_k=top_k)
