"""Unit tests for SearchService strategy selection."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import RetrievalConfig, RetrievalStrategy
from app.modules.retrieval.retrievers.models import CandidateHit, CandidateSource
from app.modules.retrieval.schemas.search import SearchRequest
from app.modules.retrieval.services.search_service import SearchService
from app.modules.retrieval.schemas.search import RetrievalResult

pytestmark = pytest.mark.unit


async def test_search_service_uses_request_strategy_override() -> None:
    project_id = uuid.uuid4()
    session = AsyncMock()
    embedder = MagicMock()
    vector_store = MagicMock()
    reranker = MagicMock()
    config = RetrievalConfig(strategy=RetrievalStrategy.SEMANTIC)

    service = SearchService(
        session=session,
        project_id=project_id,
        embedder=embedder,
        vector_store=vector_store,
        reranker=reranker,
        retrieval_config=config,
        ensure_project=AsyncMock(),
    )

    retriever = MagicMock()
    retriever.retrieve = AsyncMock(
        return_value=[CandidateHit(uuid.uuid4(), 0.5, CandidateSource.SEMANTIC)]
    )
    service._build_retriever = MagicMock(return_value=retriever)  # type: ignore[method-assign]
    service._hydrator = MagicMock()
    service._hydrator.hydrate = AsyncMock(
        return_value=[
            RetrievalResult(
                chunk_id=uuid.uuid4(),
                document_id=uuid.uuid4(),
                chunk_index=0,
                content="x",
                score=0.5,
                filename="f.txt",
            )
        ]
    )

    response = await service.search(
        SearchRequest(query="test", strategy=RetrievalStrategy.HYBRID)
    )

    service._build_retriever.assert_called_once_with(RetrievalStrategy.HYBRID)
    assert response.top_k == config.default_top_k
