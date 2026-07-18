"""Embedding-backed learned reranker using an existing embedding provider."""

from __future__ import annotations

import math

import regex

from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.contracts.reranker import (
    BaseRerankerProvider,
    RerankRequest,
    RerankResponse,
    RerankResult,
)

_SENTENCE_BOUNDARY = regex.compile(
    r"(?<=[.!?।॥。\uff01\uff1f…])\s+",
    regex.UNICODE,
)


class EmbeddingRerankerProvider(BaseRerankerProvider):
    """Rerank a bounded candidate window by learned embedding similarity.

    ``max_sentence`` reduces long-chunk dilution by scoring the best sentence.
    The provider remains useful with any configured embedding backend; quality
    runs mark the hash backend as non-learned and therefore ineligible for
    promotion.
    """

    def __init__(
        self,
        embedder: BaseEmbeddingProvider,
        *,
        max_sentence: bool = False,
    ) -> None:
        self._embedder = embedder
        self._max_sentence = max_sentence

    @property
    def provider_name(self) -> str:
        return "embedding_max" if self._max_sentence else "embedding"

    @property
    def model_name(self) -> str:
        mode = "max-sentence" if self._max_sentence else "whole-chunk"
        return f"{self._embedder.model_name}:{mode}"

    @property
    def provider_version(self) -> str:
        return self._embedder.provider_version

    async def rerank(self, request: RerankRequest) -> RerankResponse:
        passages: list[str] = []
        passage_owners: list[int] = []
        for candidate_index, candidate in enumerate(request.candidates):
            candidate_passages = self._passages(candidate.text)
            passages.extend(candidate_passages)
            passage_owners.extend([candidate_index] * len(candidate_passages))

        embedded = await self._embedder.embed_texts([request.query, *passages])
        query_vector = embedded.vectors[0]
        best_scores = [-1.0] * len(request.candidates)
        for owner, vector in zip(passage_owners, embedded.vectors[1:], strict=True):
            similarity = _cosine_similarity(query_vector, vector)
            best_scores[owner] = max(best_scores[owner], similarity)

        results = [
            RerankResult(
                chunk_id=candidate.chunk_id,
                score=max(0.0, min(1.0, (best_scores[index] + 1.0) / 2.0)),
                metadata={
                    **candidate.metadata,
                    "source_score": candidate.source_score,
                    "learned": self._embedder.provider_name != "hash",
                },
            )
            for index, candidate in enumerate(request.candidates)
        ]
        results.sort(key=lambda item: (-item.score, str(item.chunk_id)))
        return RerankResponse(
            results=results[: request.top_n],
            provider=self.provider_name,
            model=self.model_name,
            provider_version=self.provider_version,
        )

    def _passages(self, text: str) -> list[str]:
        if not self._max_sentence:
            return [text]
        sentences = [part.strip() for part in _SENTENCE_BOUNDARY.split(text) if part.strip()]
        return sentences or [text]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)
