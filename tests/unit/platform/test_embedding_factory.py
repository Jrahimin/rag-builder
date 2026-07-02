"""Unit tests for embedding provider factory."""

from __future__ import annotations

import pytest

from app.core.config import EmbeddingBackend, EmbeddingConfig, Settings
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.embedding_factory import create_embedding_provider
from app.platform.providers.implementations.gemini_embedding import GeminiEmbeddingProvider
from app.platform.providers.implementations.openai_embedding import OpenAIEmbeddingProvider


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
