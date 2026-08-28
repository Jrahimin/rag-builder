"""Regression coverage for semantic sentence-embedding batches."""

from __future__ import annotations

import pytest

from app.modules.knowledge.services.chunking.sentence_similarity_service import (
    SentenceSimilarityService,
)
from app.platform.providers.contracts.embedding import EmbeddingBatchResult, EmbeddingPurpose

pytestmark = pytest.mark.unit


class _EmbeddingProvider:
    provider_name = "test"
    model_name = "test-model"
    provider_version = "1"
    dimensions = 2

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    async def embed_texts(
        self,
        texts: list[str],
        *,
        purpose: EmbeddingPurpose = EmbeddingPurpose.DOCUMENT,
    ) -> EmbeddingBatchResult:
        assert purpose is EmbeddingPurpose.DOCUMENT
        self.batches.append(texts)
        return EmbeddingBatchResult(
            vectors=[[1.0, 0.0] for _ in texts],
            provider=self.provider_name,
            model=self.model_name,
            dimensions=self.dimensions,
            provider_version=self.provider_version,
        )


async def test_sentence_similarity_respects_configured_embedding_batch_size() -> None:
    embedder = _EmbeddingProvider()
    service = SentenceSimilarityService(embedder, batch_size=2)  # type: ignore[arg-type]

    result = await service.detect_boundaries(
        ["one", "two", "three", "four", "five"],
        drop_threshold=0.5,
    )

    assert [len(batch) for batch in embedder.batches] == [2, 2, 1]
    assert result.boundaries == ()
