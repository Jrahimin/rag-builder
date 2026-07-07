"""Semantic boundary detection via sentence similarity."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.platform.providers.contracts.embedding import BaseEmbeddingProvider


@dataclass(frozen=True, slots=True)
class BoundaryDetectionResult:
    """Boundary candidates and provider metadata for a chunking run."""

    boundaries: tuple[int, ...]
    provider: str
    model: str
    provider_version: str


class BaseSentenceSimilarityService(ABC):
    """Detect semantic boundaries between sentences."""

    @abstractmethod
    async def detect_boundaries(
        self,
        sentences: list[str],
        *,
        drop_threshold: float,
    ) -> BoundaryDetectionResult:
        """Return sentence indices where new chunks should start."""


class SentenceSimilarityService(BaseSentenceSimilarityService):
    """Embedding-backed sentence similarity boundary detector."""

    def __init__(self, embedder: BaseEmbeddingProvider) -> None:
        self._embedder = embedder

    async def detect_boundaries(
        self,
        sentences: list[str],
        *,
        drop_threshold: float,
    ) -> BoundaryDetectionResult:
        if len(sentences) < 2:
            return BoundaryDetectionResult(
                boundaries=(),
                provider=self._embedder.provider_name,
                model=self._embedder.model_name,
                provider_version=self._embedder.provider_version,
            )

        embedded = await self._embedder.embed_texts(sentences)
        vectors = embedded.vectors
        boundaries: list[int] = []
        for index in range(1, len(sentences)):
            similarity = _cosine_similarity(vectors[index - 1], vectors[index])
            if similarity <= drop_threshold:
                boundaries.append(index)

        return BoundaryDetectionResult(
            boundaries=tuple(boundaries),
            provider=embedded.provider,
            model=embedded.model,
            provider_version=embedded.provider_version,
        )


class HashSentenceSimilarityService(BaseSentenceSimilarityService):
    """Deterministic offline boundary detector for tests and hash embeddings."""

    async def detect_boundaries(
        self,
        sentences: list[str],
        *,
        drop_threshold: float,
    ) -> BoundaryDetectionResult:
        del drop_threshold
        boundaries: list[int] = []
        for index in range(1, len(sentences)):
            left = sentences[index - 1].strip().lower()
            right = sentences[index].strip().lower()
            if not left or not right:
                boundaries.append(index)
                continue
            overlap = len(set(left.split()) & set(right.split()))
            union = len(set(left.split()) | set(right.split())) or 1
            if overlap / union < 0.2:
                boundaries.append(index)
        return BoundaryDetectionResult(
            boundaries=tuple(boundaries),
            provider="hash",
            model="local",
            provider_version="1",
        )


def split_sentences(text: str) -> list[str]:
    """Deterministic multilingual sentence splitter."""
    import regex

    from app.platform.domain.text_normalizer import normalize_for_storage

    normalized = normalize_for_storage(text)
    if not normalized:
        return []
    parts = regex.split(r"(?<=[.!?।॥。！？…])\s+", normalized)
    return [part.strip() for part in parts if part.strip()]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
