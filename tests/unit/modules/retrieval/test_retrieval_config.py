"""Unit tests for retrieval configuration."""

from __future__ import annotations

import pytest

from app.core.config import RetrievalConfig, RetrievalStrategy, RerankerBackend

pytestmark = pytest.mark.unit


def test_retrieval_config_defaults_to_semantic_strategy() -> None:
    config = RetrievalConfig()
    assert config.strategy is RetrievalStrategy.SEMANTIC
    assert config.rerank_enabled is True
    assert config.reranker_backend is RerankerBackend.LEXICAL


def test_retrieval_config_accepts_hybrid_strategy_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APE_RETRIEVAL__STRATEGY", "hybrid")
    monkeypatch.setenv("APE_RETRIEVAL__RERANK_ENABLED", "false")
    from app.core.config import Settings

    settings = Settings()
    assert settings.retrieval.strategy is RetrievalStrategy.HYBRID
    assert settings.retrieval.rerank_enabled is False
