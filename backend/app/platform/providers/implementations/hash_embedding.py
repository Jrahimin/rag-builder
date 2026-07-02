"""Deterministic hash-based embedding provider for tests and local dev."""

from __future__ import annotations

import hashlib
import math

from app.platform.providers.contracts.embedding import BaseEmbeddingProvider, EmbeddingBatchResult


def _hash_to_vector(text: str, dimensions: int) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = [(digest[i % len(digest)] / 255.0) * 2.0 - 1.0 for i in range(dimensions)]
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


class HashEmbeddingProvider(BaseEmbeddingProvider):
    """Produce normalized pseudo-embeddings from content hashes."""

    def __init__(self, *, model: str, dimensions: int, provider_version: str) -> None:
        self._model = model
        self._dimensions = dimensions
        self._provider_version = provider_version

    @property
    def provider_name(self) -> str:
        return "hash"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def provider_version(self) -> str:
        return self._provider_version

    async def embed_texts(self, texts: list[str]) -> EmbeddingBatchResult:
        vectors = [_hash_to_vector(text, self._dimensions) for text in texts]
        return EmbeddingBatchResult(
            vectors=vectors,
            provider=self.provider_name,
            model=self.model_name,
            dimensions=self._dimensions,
            provider_version=self._provider_version,
        )
