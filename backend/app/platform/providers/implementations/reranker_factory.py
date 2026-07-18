"""Reranker provider factory."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import RerankerBackend, Settings, get_settings
from app.platform.providers.contracts.reranker import BaseRerankerProvider
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.embedding_factory import create_embedding_provider
from app.platform.providers.implementations.embedding_reranker import EmbeddingRerankerProvider
from app.platform.providers.implementations.lexical_reranker import LexicalRerankerProvider
from app.platform.providers.implementations.noop_reranker import NoopRerankerProvider


def create_reranker_provider(settings: Settings) -> BaseRerankerProvider:
    backend = settings.retrieval.reranker_backend
    if backend is RerankerBackend.NOOP:
        return NoopRerankerProvider()
    if backend is RerankerBackend.LEXICAL:
        return LexicalRerankerProvider()
    if backend is RerankerBackend.EMBEDDING:
        return EmbeddingRerankerProvider(create_embedding_provider(settings))
    if backend is RerankerBackend.EMBEDDING_MAX:
        return EmbeddingRerankerProvider(
            create_embedding_provider(settings),
            max_sentence=True,
        )
    msg = f"Unsupported reranker backend: {backend!r}"
    raise ProviderError(msg, provider_name="reranker_factory")


@lru_cache
def get_reranker_provider() -> BaseRerankerProvider:
    """Return the process-scoped reranker provider."""
    return create_reranker_provider(get_settings())
