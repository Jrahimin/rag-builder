"""Embedding provider factory."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import EmbeddingBackend, Settings, get_settings
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.cohere_embedding import CohereEmbeddingProvider
from app.platform.providers.implementations.gemini_embedding import GeminiEmbeddingProvider
from app.platform.providers.implementations.hash_embedding import HashEmbeddingProvider
from app.platform.providers.implementations.ollama_embedding import OllamaEmbeddingProvider
from app.platform.providers.implementations.openai_embedding import OpenAIEmbeddingProvider


def create_embedding_provider(
    settings: Settings,
    *,
    backend: EmbeddingBackend | None = None,
    model: str | None = None,
    dimensions: int | None = None,
) -> BaseEmbeddingProvider:
    """Build an embedding provider.

    Query search must pass the active-build identity (backend/model/dimensions)
    while keeping live credentials. New index builds use process settings as-is.
    """
    cfg = settings.embedding
    if backend is not None or model is not None or dimensions is not None:
        updates: dict[str, object] = {}
        if backend is not None:
            updates["backend"] = backend
        if model is not None:
            updates["model"] = model
        if dimensions is not None:
            updates["dimensions"] = dimensions
        settings = settings.model_copy(update={"embedding": cfg.model_copy(update=updates)})
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
    if cfg.backend is EmbeddingBackend.COHERE:
        api_key = settings.resolved_cohere_api_key()
        if not api_key:
            msg = "Cohere embedding backend requires APE_COHERE__API_KEY"
            raise ProviderError(msg, provider_name="cohere")
        return CohereEmbeddingProvider(
            api_key=api_key,
            base_url=settings.resolved_cohere_base_url(),
            model=cfg.model,
            dimensions=cfg.dimensions,
            provider_version=cfg.provider_version,
        )
    msg = f"Unsupported embedding backend: {cfg.backend!r}"
    raise ProviderError(msg, provider_name="embedding_factory")


def create_embedding_provider_for_identity(
    settings: Settings,
    *,
    provider: str,
    model: str,
    dimensions: int,
) -> BaseEmbeddingProvider:
    """Construct the query embedder that matches an active or retained build."""
    try:
        backend = EmbeddingBackend(provider)
    except ValueError as exc:
        raise ProviderError(
            f"Active index build uses unsupported embedding provider {provider!r}.",
            provider_name=provider,
        ) from exc
    return create_embedding_provider(
        settings,
        backend=backend,
        model=model,
        dimensions=dimensions,
    )


@lru_cache
def get_embedding_provider() -> BaseEmbeddingProvider:
    """Return the process-scoped target embedding provider for new builds."""
    return create_embedding_provider(get_settings())
