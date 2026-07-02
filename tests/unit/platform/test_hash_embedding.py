"""Unit tests for hash embedding provider."""

from __future__ import annotations

import pytest

from app.platform.providers.implementations.hash_embedding import HashEmbeddingProvider


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hash_embedding_is_deterministic() -> None:
    provider = HashEmbeddingProvider(model="test", dimensions=8, provider_version="1")
    first = await provider.embed_texts(["hello world"])
    second = await provider.embed_texts(["hello world"])
    assert first.vectors == second.vectors
    assert first.provider == "hash"
    assert len(first.vectors[0]) == 8


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hash_embedding_differs_for_different_text() -> None:
    provider = HashEmbeddingProvider(model="test", dimensions=8, provider_version="1")
    result = await provider.embed_texts(["alpha", "beta"])
    assert result.vectors[0] != result.vectors[1]
