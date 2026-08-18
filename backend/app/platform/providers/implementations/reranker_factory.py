"""Reranker provider factory."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import RerankerBackend, Settings, get_settings
from app.platform.providers.contracts.reranker import BaseRerankerProvider
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.cohere_reranker_provider import CohereRerankerProvider
from app.platform.providers.implementations.embedding_factory import create_embedding_provider
from app.platform.providers.implementations.embedding_reranker import EmbeddingRerankerProvider
from app.platform.providers.implementations.lexical_reranker import LexicalRerankerProvider
from app.platform.providers.implementations.noop_reranker import NoopRerankerProvider


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
        api_key = (settings.reranker.cohere_api_key or "").strip()
        if not api_key:
            raise ProviderError(
                "Cohere reranker requires APE_RERANKER__COHERE_API_KEY",
                provider_name="cohere",
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
