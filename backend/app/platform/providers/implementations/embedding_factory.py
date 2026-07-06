"""Embedding provider factory."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import EmbeddingBackend, Settings, get_settings
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.gemini_embedding import GeminiEmbeddingProvider
from app.platform.providers.implementations.hash_embedding import HashEmbeddingProvider
from app.platform.providers.implementations.ollama_embedding import OllamaEmbeddingProvider
from app.platform.providers.implementations.openai_embedding import OpenAIEmbeddingProvider


def create_embedding_provider(settings: Settings) -> BaseEmbeddingProvider:
    """Build the configured embedding provider."""
    cfg = settings.embedding
    if cfg.backend is EmbeddingBackend.HASH:
        return HashEmbeddingProvider(
            model=cfg.model,
            dimensions=cfg.dimensions,
            provider_version=cfg.provider_version,
        )
    if cfg.backend is EmbeddingBackend.OLLAMA:
        return OllamaEmbeddingProvider(
            base_url=cfg.ollama_base_url,
            model=cfg.model,
            dimensions=cfg.dimensions,
            provider_version=cfg.provider_version,
        )
    if cfg.backend is EmbeddingBackend.OPENAI:
        if not cfg.openai_api_key:
            msg = "OpenAI embedding backend requires APE_EMBEDDING__OPENAI_API_KEY"
            raise ProviderError(msg, provider_name="openai")
        return OpenAIEmbeddingProvider(
            api_key=cfg.openai_api_key,
            base_url=cfg.openai_base_url,
            model=cfg.model,
            dimensions=cfg.dimensions,
            provider_version=cfg.provider_version,
        )
    if cfg.backend is EmbeddingBackend.GEMINI:
        if not cfg.gemini_api_key:
            msg = "Gemini embedding backend requires APE_EMBEDDING__GEMINI_API_KEY"
            raise ProviderError(msg, provider_name="gemini")
        return GeminiEmbeddingProvider(
            api_key=cfg.gemini_api_key,
            base_url=cfg.gemini_base_url,
            model=cfg.model,
            dimensions=cfg.dimensions,
            provider_version=cfg.provider_version,
        )
    msg = f"Unsupported embedding backend: {cfg.backend!r}"
    raise ProviderError(msg, provider_name="embedding_factory")


@lru_cache
def get_embedding_provider() -> BaseEmbeddingProvider:
    """Return the process-scoped embedding provider."""
    return create_embedding_provider(get_settings())
