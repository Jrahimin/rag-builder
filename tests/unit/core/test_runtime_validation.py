"""Production runtime profile validation tests."""

from __future__ import annotations

import pytest

from app.core.config import (
    AppConfig,
    AuthConfig,
    CohereConfig,
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
from app.platform.config.project_ai import resolve_project_ai_config


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
            reranker_backend=RerankerBackend.LEXICAL,
        ),
        "auth": AuthConfig(
            enabled=True,
            key_pepper="key-pepper-that-is-at-least-thirty-two-bytes",
            admin_jwt_secret="admin-jwt-secret-that-is-at-least-thirty-two-bytes",
            admin_cookie_secure=True,
        ),
    }
    values.update(updates)
    return Settings(**values)


def test_development_preserves_fake_provider_defaults() -> None:
    validate_runtime_config(Settings())


def test_certified_hosted_profile_is_accepted() -> None:
    validate_runtime_config(_production_settings())


def test_production_rejects_unconfigured_pass_through_rerank_stage() -> None:
    settings = _production_settings(
        retrieval=RetrievalConfig(
            strategy=RetrievalStrategy.HYBRID,
            reranker_backend=RerankerBackend.NOOP,
        )
    )
    with pytest.raises(ProductionConfigurationError, match="reranker backend"):
        validate_runtime_config(settings)


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
                rerank_mode="off",
                reranker_backend=RerankerBackend.NOOP,
            ),
            "rerank stage",
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

    with pytest.raises(ProductionConfigurationError, match="GOOGLE_API_KEY"):
        validate_runtime_config(
            Settings(
                ocr=OcrConfig(
                    enabled=True,
                    bangla_backend=OcrBackend.GOOGLE_VISION,
                )
            )
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


def test_hosted_openai_does_not_require_cohere() -> None:
    validate_runtime_config(_production_settings())


def test_hosted_managed_requires_cohere_embeddings_and_shared_key() -> None:
    validate_runtime_config(
        _production_settings(
            runtime=RuntimeConfig(profile=RuntimeProfile.HOSTED_MANAGED),
            embedding=EmbeddingConfig(
                backend=EmbeddingBackend.COHERE,
                model="embed-v4.0",
                dimensions=1024,
            ),
            cohere=CohereConfig(api_key="cohere-secret"),
        )
    )


def test_hosted_managed_rejects_openai_embeddings() -> None:
    with pytest.raises(ProductionConfigurationError, match="hosted_managed requires"):
        validate_runtime_config(
            _production_settings(runtime=RuntimeConfig(profile=RuntimeProfile.HOSTED_MANAGED))
        )


def test_hosted_managed_accepts_legacy_reranker_key_as_shared_secret() -> None:
    validate_runtime_config(
        _production_settings(
            runtime=RuntimeConfig(profile=RuntimeProfile.HOSTED_MANAGED),
            embedding=EmbeddingConfig(
                backend=EmbeddingBackend.COHERE,
                model="embed-v4.0",
                dimensions=1024,
            ),
            reranker={"cohere_api_key": "legacy-cohere-secret"},
        )
    )


def test_expected_hosted_production_effective_configuration_resolves_exactly() -> None:
    settings = _production_settings(
        runtime=RuntimeConfig(profile=RuntimeProfile.HOSTED_MANAGED),
        embedding=EmbeddingConfig(
            backend=EmbeddingBackend.COHERE,
            model="embed-v4.0",
            dimensions=1024,
        ),
        llm=LLMConfig(
            backend=LLMBackend.OPENAI,
            model="gpt-5.6-luna",
            openai_api_key="llm-secret",
        ),
        cohere=CohereConfig(api_key="cohere-secret"),
        retrieval=RetrievalConfig(
            strategy=RetrievalStrategy.HYBRID,
            reranker_backend=RerankerBackend.COHERE,
            rerank_mode="always",
            rerank_candidate_window=25,
        ),
        query_translation={"enabled": False},
        ai_policy={
            "default_generation_model_id": "openai-gpt-5.6-luna",
            "allowed_generation_model_ids": ["openai-gpt-5.6-luna"],
        },
    )

    validate_runtime_config(settings)
    effective = resolve_project_ai_config(settings, None)

    assert effective.configuration.llm.generation_model_id == "openai-gpt-5.6-luna"
    assert effective.configuration.llm.provider == "openai"
    assert effective.configuration.llm.model == "gpt-5.6-luna"
    assert effective.configuration.retrieval.strategy == "hybrid"
    assert effective.configuration.retrieval.rerank_mode == "always"
    assert effective.configuration.retrieval.reranker_backend == "cohere"
    assert effective.configuration.retrieval.rerank_candidate_window == 25
    assert effective.configuration.retrieval.query_translation_enabled is False
    assert effective.configuration.chat.evidence_gate_mode == "enforce"
    assert effective.configuration.chat.include_citations is True
    assert effective.configuration.source_policy_mode == "enforce"
    assert effective.invariants.model_dump() == {
        "hybrid_retrieval": True,
        "hosted_reranking_stage": True,
        "evidence_gate_enforced": True,
        "content_hash_deduplication": True,
        "durable_citation_provenance": True,
        "governed_source_policy": True,
        "governed_modifies_expansion": True,
    }


def test_capability_profile_accepts_underscore_runtime_alias() -> None:
    runtime = RuntimeConfig(capability_profile_id="hosted_managed")

    assert runtime.capability_profile_id == "hosted-managed"


def test_capability_profile_is_authoritative_over_legacy_runtime_alias() -> None:
    settings = _production_settings(
        runtime=RuntimeConfig(capability_profile_id="hosted-openai"),
        embedding=EmbeddingConfig(
            backend=EmbeddingBackend.OPENAI,
            model="text-embedding-3-large",
            dimensions=1024,
            openai_api_key="embedding-secret",
        ),
        retrieval=RetrievalConfig(reranker_backend=RerankerBackend.COHERE),
        cohere=CohereConfig(api_key="cohere-secret"),
    )

    validate_runtime_config(settings)
