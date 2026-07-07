"""Unit tests for SemanticRetriever candidate output."""

from __future__ import annotations

from dataclasses import replace

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import RetrievalStrategy
from app.modules.retrieval.retrievers.models import CandidateSource, RetrievalContext, RetrievalFilters
from app.modules.retrieval.retrievers.semantic_retriever import SemanticRetriever
from app.platform.providers.contracts.vector_store import VectorPoint
from app.platform.providers.implementations.hash_embedding import HashEmbeddingProvider
from app.platform.providers.implementations.memory_vector_store import MemoryVectorStoreProvider

pytestmark = pytest.mark.unit

_DIMENSIONS = 16


def _embedder() -> HashEmbeddingProvider:
    return HashEmbeddingProvider(model="m", dimensions=_DIMENSIONS, provider_version="1")


def _context(
    project_id: uuid.UUID,
    *,
    embedding_set_version: int = 1,
    strategy: RetrievalStrategy = RetrievalStrategy.SEMANTIC,
    top_k: int = 10,
) -> RetrievalContext:
    return RetrievalContext(
        project_id=project_id,
        query="chunk content",
        embedding_set_version=embedding_set_version,
        filters=RetrievalFilters(),
        top_k=top_k,
        strategy=strategy,
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


async def _seed_point(
    store: MemoryVectorStoreProvider,
    *,
    point_id: str,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    embedding_set_version: int,
    text: str = "chunk content",
) -> None:
    vectors = (await _embedder().embed_texts([text])).vectors
    await store.upsert_points(
        [
            VectorPoint(
                point_id=point_id,
                vector=vectors[0],
                payload={
                    "project_id": str(project_id),
                    "document_id": str(document_id),
                    "embedding_set_version": embedding_set_version,
                    "source": "handbook",
                },
            )
        ]
    )


async def test_semantic_retriever_returns_candidate_hits_only() -> None:
    project_id = uuid.uuid4()
    store = MemoryVectorStoreProvider()
    document_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    await _seed_point(
        store,
        point_id=str(chunk_id),
        project_id=project_id,
        document_id=document_id,
        embedding_set_version=1,
    )

    retriever = SemanticRetriever(
        session=AsyncMock(),
        project_id=project_id,
        embedder=_embedder(),
        vector_store=store,
    )
    hits = await retriever.retrieve(_context(project_id))

    assert len(hits) == 1
    assert hits[0].chunk_id == chunk_id
    assert hits[0].source is CandidateSource.SEMANTIC
    assert hits[0].score > 0


async def test_semantic_retriever_filters_by_embedding_set_version() -> None:
    project_id = uuid.uuid4()
    store = MemoryVectorStoreProvider()
    document_id = uuid.uuid4()
    current = uuid.uuid4()
    stale = uuid.uuid4()
    await _seed_point(
        store,
        point_id=str(current),
        project_id=project_id,
        document_id=document_id,
        embedding_set_version=2,
    )
    await _seed_point(
        store,
        point_id=str(stale),
        project_id=project_id,
        document_id=document_id,
        embedding_set_version=1,
    )

    retriever = SemanticRetriever(
        session=AsyncMock(),
        project_id=project_id,
        embedder=_embedder(),
        vector_store=store,
    )
    hits = await retriever.retrieve(_context(project_id, embedding_set_version=2))

    assert [hit.chunk_id for hit in hits] == [current]


async def test_semantic_retriever_strips_unfilterable_metadata() -> None:
    project_id = uuid.uuid4()
    store = MemoryVectorStoreProvider()
    document_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    await _seed_point(
        store,
        point_id=str(chunk_id),
        project_id=project_id,
        document_id=document_id,
        embedding_set_version=1,
    )

    retriever = SemanticRetriever(
        session=AsyncMock(),
        project_id=project_id,
        embedder=_embedder(),
        vector_store=store,
    )
    context = RetrievalContext(
        project_id=project_id,
        query="chunk content",
        embedding_set_version=1,
        filters=RetrievalFilters(metadata={"secret": "nope"}),
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
    hits = await retriever.retrieve(context)
    assert len(hits) == 1

    context_filtered = replace(
        context,
        filters=RetrievalFilters(metadata={"source": "other"}),
    )
    hits_filtered = await retriever.retrieve(context_filtered)
    assert hits_filtered == []
