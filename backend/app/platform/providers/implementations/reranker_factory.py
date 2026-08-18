"""Reranker provider factory."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import RerankerBackend, Settings, get_settings
from app.platform.providers.contracts.reranker import (
    BaseRerankerProvider,
    RerankRequest,
    RerankResponse,
)
from app.platform.providers.errors import ProviderError, ProviderUnavailableError
from app.platform.providers.implementations.cohere_reranker_provider import CohereRerankerProvider
from app.platform.providers.implementations.embedding_factory import create_embedding_provider
from app.platform.providers.implementations.embedding_reranker import EmbeddingRerankerProvider
from app.platform.providers.implementations.lexical_reranker import LexicalRerankerProvider
from app.platform.providers.implementations.noop_reranker import NoopRerankerProvider


class UnavailableRerankerProvider(BaseRerankerProvider):
    """Selected managed reranker cannot run; hybrid falls back to fused RRF."""

    def __init__(self, *, provider_name: str, model: str, reason: str) -> None:
        self._provider_name = provider_name
        self._model = model
        self._reason = reason

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider_version(self) -> str:
        return "unavailable"

    async def rerank(self, request: RerankRequest) -> RerankResponse:
        del request
        raise ProviderUnavailableError(
            self._reason,
            provider_name=self._provider_name,
        )


def create_reranker_provider(
    settings: Settings,
    *,
    backend: RerankerBackend | None = None,
) -> BaseRerankerProvider:
    selected = backend or settings.retrieval.reranker_backend
    if selected is RerankerBackend.NOOP:
        return NoopRerankerProvider()
    if selected is RerankerBackend.LEXICAL:
        return LexicalRerankerProvider()
    if selected is RerankerBackend.EMBEDDING:
        return EmbeddingRerankerProvider(create_embedding_provider(settings))
    if selected is RerankerBackend.EMBEDDING_MAX:
        return EmbeddingRerankerProvider(
            create_embedding_provider(settings),
            max_sentence=True,
        )
    if selected is RerankerBackend.COHERE:
        api_key = settings.resolved_cohere_api_key()
        if not api_key:
            return UnavailableRerankerProvider(
                provider_name="cohere",
                model=settings.reranker.cohere_model,
                reason="Cohere reranker is not configured.",
            )
        return CohereRerankerProvider(
            api_key=api_key,
            base_url=settings.reranker.cohere_base_url,
            model=settings.reranker.cohere_model,
            provider_version=settings.reranker.provider_version,
            request_timeout_seconds=settings.reranker.request_timeout_seconds,
        )
    msg = f"Unsupported reranker backend: {selected!r}"
    raise ProviderError(msg, provider_name="reranker_factory")


@lru_cache
def get_reranker_provider() -> BaseRerankerProvider:
    """Return the process-scoped reranker provider."""
    return create_reranker_provider(get_settings())
