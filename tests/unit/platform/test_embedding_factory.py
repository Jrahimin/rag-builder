"""Unit tests for embedding provider factory."""

from __future__ import annotations

import pytest

from app.core.config import (
    CohereConfig,
    EmbeddingBackend,
    EmbeddingConfig,
    RerankerBackend,
    RerankerProviderConfig,
    RetrievalConfig,
    Settings,
)
from app.platform.providers.errors import ProviderError, ProviderUnavailableError
from app.platform.providers.implementations.cohere_embedding import CohereEmbeddingProvider
from app.platform.providers.implementations.embedding_factory import (
    create_embedding_provider,
    create_embedding_provider_for_identity,
)
from app.platform.providers.implementations.gemini_embedding import GeminiEmbeddingProvider
from app.platform.providers.implementations.openai_embedding import OpenAIEmbeddingProvider
from app.platform.providers.implementations.reranker_factory import (
    UnavailableRerankerProvider,
    create_reranker_provider,
)


def _settings(**embedding_overrides: object) -> Settings:
    return Settings(embedding=EmbeddingConfig(**embedding_overrides))


@pytest.mark.unit
def test_factory_openai_requires_api_key() -> None:
    with pytest.raises(ProviderError, match="OPENAI_API_KEY"):
        create_embedding_provider(_settings(backend=EmbeddingBackend.OPENAI))


@pytest.mark.unit
def test_factory_openai_returns_provider() -> None:
    provider = create_embedding_provider(
        _settings(
            backend=EmbeddingBackend.OPENAI,
            openai_api_key="sk-test",
            model="text-embedding-3-small",
            dimensions=1536,
        )
    )
    assert isinstance(provider, OpenAIEmbeddingProvider)
    assert provider.provider_name == "openai"
    assert provider.model_name == "text-embedding-3-small"


@pytest.mark.unit
def test_factory_gemini_requires_api_key() -> None:
    with pytest.raises(ProviderError, match="GEMINI_API_KEY"):
        create_embedding_provider(_settings(backend=EmbeddingBackend.GEMINI))


@pytest.mark.unit
def test_factory_gemini_returns_provider() -> None:
    provider = create_embedding_provider(
        _settings(
            backend=EmbeddingBackend.GEMINI,
            gemini_api_key="gemini-test",
            model="text-embedding-004",
            dimensions=768,
        )
    )
    assert isinstance(provider, GeminiEmbeddingProvider)
    assert provider.provider_name == "gemini"
    assert provider.model_name == "text-embedding-004"


@pytest.mark.unit
def test_factory_cohere_requires_shared_key() -> None:
    with pytest.raises(ProviderError, match="APE_COHERE__API_KEY"):
        create_embedding_provider(_settings(backend=EmbeddingBackend.COHERE, model="embed-v4.0"))


@pytest.mark.unit
def test_factory_cohere_uses_canonical_then_legacy_key() -> None:
    canonical = create_embedding_provider(
        Settings(
            embedding=EmbeddingConfig(backend=EmbeddingBackend.COHERE, model="embed-v4.0"),
            cohere=CohereConfig(api_key="canonical-key"),
            reranker=RerankerProviderConfig(cohere_api_key="legacy-key"),
        )
    )
    assert isinstance(canonical, CohereEmbeddingProvider)

    legacy = create_embedding_provider(
        Settings(
            embedding=EmbeddingConfig(backend=EmbeddingBackend.COHERE, model="embed-v4.0"),
            reranker=RerankerProviderConfig(cohere_api_key="legacy-key"),
        )
    )
    assert isinstance(legacy, CohereEmbeddingProvider)


@pytest.mark.unit
async def test_reranker_factory_missing_key_raises_unavailable_on_rerank() -> None:
    from app.platform.providers.contracts.reranker import RerankRequest

    provider = create_reranker_provider(
        Settings(retrieval=RetrievalConfig(reranker_backend=RerankerBackend.COHERE))
    )
    assert isinstance(provider, UnavailableRerankerProvider)
    with pytest.raises(ProviderUnavailableError):
        await provider.rerank(RerankRequest(query="q", candidates=[], top_n=1))


@pytest.mark.unit
def test_factory_builds_query_embedder_from_active_identity_not_live_backend() -> None:
    settings = Settings(
        embedding=EmbeddingConfig(
            backend=EmbeddingBackend.COHERE,
            model="embed-v4.0",
            openai_api_key="sk-test",
        ),
        cohere=CohereConfig(api_key="cohere-key"),
    )
    provider = create_embedding_provider_for_identity(
        settings,
        provider="openai",
        model="text-embedding-3-large",
        dimensions=1024,
    )
    assert isinstance(provider, OpenAIEmbeddingProvider)
    assert provider.model_name == "text-embedding-3-large"
