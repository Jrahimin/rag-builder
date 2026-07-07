"""Lexical overlap reranker — self-hosted, no external API."""

from __future__ import annotations

from app.platform.domain.text_tokenization import tokenize
from app.platform.providers.contracts.reranker import (
    BaseRerankerProvider,
    RerankRequest,
    RerankResponse,
    RerankResult,
)


class LexicalRerankerProvider(BaseRerankerProvider):
    """Token-overlap reranker suitable for local/self-hosted deployments."""

    def __init__(
        self,
        *,
        model: str = "lexical-overlap",
        provider_version: str = "1",
    ) -> None:
        self._model = model
        self._provider_version = provider_version

    @property
    def provider_name(self) -> str:
        return "lexical"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider_version(self) -> str:
        return self._provider_version

    async def rerank(self, request: RerankRequest) -> RerankResponse:
        query_terms = set(tokenize(request.query, for_query=True))
        scored: list[RerankResult] = []
        for candidate in request.candidates:
            doc_terms = set(tokenize(candidate.text))
            overlap = len(query_terms & doc_terms)
            union = len(query_terms | doc_terms) or 1
            jaccard = overlap / union
            score = (0.7 * jaccard) + (0.3 * min(candidate.source_score, 1.0))
            scored.append(
                RerankResult(
                    chunk_id=candidate.chunk_id,
                    score=score,
                    metadata=dict(candidate.metadata),
                )
            )
        scored.sort(key=lambda item: (-item.score, str(item.chunk_id)))
        return RerankResponse(
            results=scored[: request.top_n],
            provider=self.provider_name,
            model=self.model_name,
            provider_version=self.provider_version,
        )
