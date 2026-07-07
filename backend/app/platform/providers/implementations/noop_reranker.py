"""No-op reranker — preserves fused order and scores."""

from __future__ import annotations

from app.platform.providers.contracts.reranker import (
    BaseRerankerProvider,
    RerankRequest,
    RerankResponse,
    RerankResult,
)


class NoopRerankerProvider(BaseRerankerProvider):
    """Pass-through reranker for tests and explicit disable paths."""

    def __init__(
        self,
        *,
        model: str = "noop",
        provider_version: str = "1",
    ) -> None:
        self._model = model
        self._provider_version = provider_version

    @property
    def provider_name(self) -> str:
        return "noop"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider_version(self) -> str:
        return self._provider_version

    async def rerank(self, request: RerankRequest) -> RerankResponse:
        results = [
            RerankResult(
                chunk_id=candidate.chunk_id,
                score=candidate.source_score,
                metadata=dict(candidate.metadata),
            )
            for candidate in request.candidates[: request.top_n]
        ]
        return RerankResponse(
            results=results,
            provider=self.provider_name,
            model=self.model_name,
            provider_version=self.provider_version,
        )
