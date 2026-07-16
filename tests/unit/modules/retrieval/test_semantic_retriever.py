"""Unit tests for repository-backed semantic candidate retrieval."""

from __future__ import annotations

import uuid
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import RetrievalStrategy
from app.modules.retrieval.repositories.chunk_embedding_repository import ChunkEmbeddingRepository
from app.modules.retrieval.retrievers.models import (
    CandidateHit,
    CandidateSource,
    RetrievalContext,
    RetrievalFilters,
)
from app.modules.retrieval.retrievers.semantic_retriever import SemanticRetriever
from app.platform.providers.implementations.hash_embedding import HashEmbeddingProvider

pytestmark = pytest.mark.unit

_DIMENSIONS = 16


def _embedder() -> HashEmbeddingProvider:
    return HashEmbeddingProvider(model="m", dimensions=_DIMENSIONS, provider_version="1")


def _context(project_id: uuid.UUID, **changes: object) -> RetrievalContext:
    context = RetrievalContext(
        project_id=project_id,
        query="chunk content",
        embedding_set_version=1,
        filters=RetrievalFilters(),
        top_k=10,
        strategy=RetrievalStrategy.SEMANTIC,
        semantic_candidate_top_k=50,
        keyword_candidate_top_k=50,
        rrf_k=60,
        semantic_weight=1.0,
        keyword_weight=1.0,
        rerank_enabled=False,
        rerank_top_n=20,
        rerank_score_threshold=None,
        score_threshold=None,
        filterable_metadata_keys=("source",),
    )
    return replace(context, **changes)


def _repository(hits: list[CandidateHit]) -> MagicMock:
    repository = MagicMock(spec=ChunkEmbeddingRepository)
    repository.search_cosine = AsyncMock(return_value=hits)
    return repository


async def test_semantic_retriever_returns_repository_candidates() -> None:
    project_id = uuid.uuid4()
    hit = CandidateHit(uuid.uuid4(), 0.75, CandidateSource.SEMANTIC, {"source": "handbook"})
    repository = _repository([hit])

    retriever = SemanticRetriever(AsyncMock(), project_id, _embedder(), repository)
    hits = await retriever.retrieve(_context(project_id))

    assert hits == [hit]
    call = repository.search_cosine.await_args.kwargs
    assert call["embedding_set_version"] == 1
    assert call["provider"] == "hash"
    assert call["model"] == "m"
    assert call["top_k"] == 10


async def test_semantic_retriever_uses_hybrid_candidate_window() -> None:
    project_id = uuid.uuid4()
    repository = _repository([])
    retriever = SemanticRetriever(AsyncMock(), project_id, _embedder(), repository)

    await retriever.retrieve(
        _context(project_id, strategy=RetrievalStrategy.HYBRID, top_k=5)
    )

    assert repository.search_cosine.await_args.kwargs["top_k"] == 50


async def test_semantic_retriever_passes_only_allowlisted_metadata() -> None:
    project_id = uuid.uuid4()
    repository = _repository([])
    retriever = SemanticRetriever(AsyncMock(), project_id, _embedder(), repository)
    context = _context(
        project_id,
        filters=RetrievalFilters(
            document_id=uuid.uuid4(),
            metadata={"source": "handbook", "secret": "nope"},
        ),
        score_threshold=0.4,
        hnsw_ef_search=120,
    )

    await retriever.retrieve(context)

    call = repository.search_cosine.await_args.kwargs
    assert call["document_id"] == context.filters.document_id
    assert call["metadata_filter"] == {"source": "handbook"}
    assert call["score_threshold"] == 0.4
    assert call["hnsw_ef_search"] == 120
