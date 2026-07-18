"""Production runtime profile validation tests."""

from __future__ import annotations

import pytest

from app.core.config import (
    AppConfig,
    AuthConfig,
    DatabaseConfig,
    EmbeddingBackend,
    EmbeddingConfig,
    Environment,
    JobQueueBackend,
    JobsConfig,
    LLMBackend,
    LLMConfig,
    MalwareScanConfig,
    MalwareScannerBackend,
    MinioConfig,
    OcrBackend,
    OcrConfig,
    RedisConfig,
    RerankerBackend,
    RetrievalConfig,
    RetrievalStrategy,
    RuntimeConfig,
    RuntimeProfile,
    Settings,
    StorageBackend,
    StorageConfig,
)
from app.core.runtime_validation import ProductionConfigurationError, validate_runtime_config


def _production_settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "app": AppConfig(env=Environment.PRODUCTION),
        "runtime": RuntimeConfig(profile=RuntimeProfile.HOSTED_OPENAI),
        "database": DatabaseConfig(password="database-secret"),
        "redis": RedisConfig(password="redis-secret"),
        "minio": MinioConfig(access_key="storage-user", secret_key="storage-secret"),
        "storage": StorageConfig(backend=StorageBackend.MINIO),
        "malware_scan": MalwareScanConfig(backend=MalwareScannerBackend.CLAMAV),
        "jobs": JobsConfig(backend=JobQueueBackend.TASKIQ, dispatcher_enabled=True),
        "embedding": EmbeddingConfig(
            backend=EmbeddingBackend.OPENAI,
            openai_api_key="embedding-secret",
            dimensions=1536,
        ),
        "llm": LLMConfig(backend=LLMBackend.OPENAI, openai_api_key="llm-secret"),
        "retrieval": RetrievalConfig(
            strategy=RetrievalStrategy.HYBRID,
            rerank_enabled=True,
            reranker_backend=RerankerBackend.LEXICAL,
        ),
        "auth": AuthConfig(enabled=True),
    }
    values.update(updates)
    return Settings(**values)


def test_development_preserves_fake_provider_defaults() -> None:
    validate_runtime_config(Settings())


def test_certified_hosted_profile_is_accepted() -> None:
    validate_runtime_config(_production_settings())


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("embedding", EmbeddingConfig(backend=EmbeddingBackend.HASH), "hash embeddings"),
        ("llm", LLMConfig(backend=LLMBackend.ECHO), "echo chat"),
        ("redis", RedisConfig(password=None), "APE_REDIS__PASSWORD"),
        (
            "retrieval",
            RetrievalConfig(
                strategy=RetrievalStrategy.HYBRID,
                rerank_enabled=True,
                reranker_backend=RerankerBackend.NOOP,
            ),
            "noop reranking",
        ),
        (
            "ocr",
            OcrConfig(enabled=True, backend=OcrBackend.NOOP),
            "Enabled OCR".lower(),
        ),
    ],
)
def test_production_rejects_fake_or_missing_capabilities(
    field: str,
    value: object,
    expected: str,
) -> None:
    with pytest.raises(ProductionConfigurationError) as exc_info:
        validate_runtime_config(_production_settings(**{field: value}))
    assert expected.lower() in str(exc_info.value).lower()


def test_private_profile_rejects_hosted_provider_combination() -> None:
    with pytest.raises(ProductionConfigurationError, match="private_ollama requires"):
        validate_runtime_config(
            _production_settings(
                runtime=RuntimeConfig(profile=RuntimeProfile.PRIVATE_OLLAMA),
            )
        )
