"""Production runtime profile validation tests."""

from __future__ import annotations

import pytest

from app.core.config import (
    AppConfig,
    AuthConfig,
    CORSConfig,
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
    WebhooksConfig,
)
from app.core.runtime_validation import ProductionConfigurationError, validate_runtime_config


def _production_settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "app": AppConfig(env=Environment.PRODUCTION),
        "runtime": RuntimeConfig(profile=RuntimeProfile.HOSTED_OPENAI),
        "cors": CORSConfig(allow_origins=["https://rag-builder.example"]),
        "database": DatabaseConfig(password="database-secret"),
        "redis": RedisConfig(password="redis-secret"),
        "minio": MinioConfig(access_key="storage-user", secret_key="storage-secret"),
        "storage": StorageConfig(backend=StorageBackend.MINIO),
        "malware_scan": MalwareScanConfig(backend=MalwareScannerBackend.CLAMAV),
        "jobs": JobsConfig(backend=JobQueueBackend.TASKIQ, dispatcher_enabled=True),
        "webhooks": WebhooksConfig(signing_key="hosted-webhook-secret" * 2),
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
        "auth": AuthConfig(
            enabled=True,
            admin_api_key="admin-key-that-is-at-least-thirty-two-bytes",
            key_pepper="key-pepper-that-is-at-least-thirty-two-bytes",
        ),
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
            "webhooks",
            WebhooksConfig(signing_key="development-only-webhook-signing-key"),
            "APE_WEBHOOKS__SIGNING_KEY",
        ),
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


def test_all_environments_reject_missing_infrastructure_configuration() -> None:
    with pytest.raises(ProductionConfigurationError, match="APE_REDIS__HOST"):
        validate_runtime_config(Settings(redis=RedisConfig(host="")))


def test_provider_selection_requires_matching_credentials() -> None:
    with pytest.raises(ProductionConfigurationError, match="OPENAI_API_KEY"):
        validate_runtime_config(
            Settings(embedding=EmbeddingConfig(backend=EmbeddingBackend.OPENAI))
        )


def test_known_fixed_embedding_dimension_is_validated() -> None:
    with pytest.raises(ProductionConfigurationError, match="DIMENSIONS=1536"):
        validate_runtime_config(
            Settings(
                embedding=EmbeddingConfig(
                    backend=EmbeddingBackend.OPENAI,
                    model="text-embedding-ada-002",
                    dimensions=384,
                    openai_api_key="test-key",
                )
            )
        )


def test_webhook_signing_and_retry_relationships_are_validated() -> None:
    with pytest.raises(ProductionConfigurationError, match="SIGNING_KEY"):
        validate_runtime_config(Settings(webhooks=WebhooksConfig(signing_key="short")))
    with pytest.raises(ProductionConfigurationError, match="retry_max_seconds"):
        validate_runtime_config(
            Settings(
                webhooks=WebhooksConfig(
                    retry_base_seconds=10,
                    retry_max_seconds=5,
                )
            )
        )


def test_production_rejects_wildcard_cors() -> None:
    with pytest.raises(ProductionConfigurationError, match="wildcard"):
        validate_runtime_config(_production_settings(cors=CORSConfig()))
