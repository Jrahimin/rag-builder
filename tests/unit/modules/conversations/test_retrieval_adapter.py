"""Unit tests for SearchServiceRetrievalAdapter."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.dependencies.conversations import SearchServiceRetrievalAdapter
from app.modules.retrieval.schemas.search import RetrievalResult, SearchResponse

pytestmark = pytest.mark.unit


async def test_adapter_maps_search_results_to_context_chunks() -> None:
    chunk_id = uuid.uuid4()
    document_id = uuid.uuid4()
    search_service = AsyncMock()
    search_service.search = AsyncMock(
        return_value=SearchResponse(
            query="refund",
            top_k=5,
            results=[
                RetrievalResult(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    chunk_index=0,
                    content="refund within 30 days",
                    score=0.91,
                    filename="policy.txt",
                )
            ],
        )
    )
    adapter = SearchServiceRetrievalAdapter(search_service)
    chunks = await adapter.retrieve(query="refund", top_k=5)
    assert len(chunks) == 1
    assert chunks[0].chunk_id == chunk_id
    assert chunks[0].filename == "policy.txt"
    assert len(chunks[0].chunk_hash) == 64
