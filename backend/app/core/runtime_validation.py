"""Fail-fast validation for the two certified production runtime profiles."""

from __future__ import annotations

from app.core.config import (
    EmbeddingBackend,
    JobQueueBackend,
    LLMBackend,
    OcrBackend,
    RerankerBackend,
    RetrievalStrategy,
    RuntimeProfile,
    Settings,
    StorageBackend,
)


class ProductionConfigurationError(ValueError):
    """Raised before application construction when production settings are unsafe."""


def validate_runtime_config(settings: Settings) -> None:
    """Reject fake, incomplete, or non-certified production combinations.

    Development and testing intentionally retain their lightweight defaults.
    """
    if not settings.app.is_production:
        return

    errors: list[str] = []
    profile = settings.runtime.profile
    if profile is RuntimeProfile.DEVELOPMENT:
        errors.append("APE_RUNTIME__PROFILE must select a certified production profile")
    if settings.embedding.backend is EmbeddingBackend.HASH:
        errors.append("hash embeddings are not allowed in production")
    if settings.llm.backend is LLMBackend.ECHO:
        errors.append("echo chat is not allowed in production")
    if settings.jobs.backend is not JobQueueBackend.TASKIQ:
        errors.append("production requires the taskiq job executor")
    if not settings.jobs.dispatcher_enabled:
        errors.append("production requires the durable outbox dispatcher")
    if settings.storage.backend is not StorageBackend.MINIO:
        errors.append("production requires MinIO/S3-compatible object storage")
    if settings.retrieval.strategy is not RetrievalStrategy.HYBRID:
        errors.append("production requires hybrid retrieval")
    if not settings.retrieval.rerank_enabled:
        errors.append("production requires reranking to be enabled")
    if settings.retrieval.reranker_backend is RerankerBackend.NOOP:
        errors.append("noop reranking is not allowed in production")
    if settings.ocr.enabled and settings.ocr.backend is OcrBackend.NOOP:
        errors.append("enabled OCR cannot use the noop backend in production")

    _require_secret(errors, "APE_DATABASE__PASSWORD", settings.database.password, {"ape"})
    _require_secret(errors, "APE_REDIS__PASSWORD", settings.redis.password)
    _require_secret(errors, "APE_MINIO__ACCESS_KEY", settings.minio.access_key, {"minioadmin"})
    _require_secret(errors, "APE_MINIO__SECRET_KEY", settings.minio.secret_key, {"minioadmin"})

    if not settings.auth.enabled:
        errors.append("production requires organization/admin authentication")

    if profile is RuntimeProfile.HOSTED_OPENAI:
        if settings.llm.backend is not LLMBackend.OPENAI:
            errors.append("hosted_openai requires APE_LLM__BACKEND=openai")
        if settings.embedding.backend is not EmbeddingBackend.OPENAI:
            errors.append("hosted_openai requires APE_EMBEDDING__BACKEND=openai")
        _require_secret(errors, "APE_LLM__OPENAI_API_KEY", settings.llm.openai_api_key)
        _require_secret(
            errors,
            "APE_EMBEDDING__OPENAI_API_KEY",
            settings.embedding.openai_api_key,
        )
    elif profile is RuntimeProfile.PRIVATE_OLLAMA:
        if settings.llm.backend is not LLMBackend.OLLAMA:
            errors.append("private_ollama requires APE_LLM__BACKEND=ollama")
        if settings.embedding.backend is not EmbeddingBackend.OLLAMA:
            errors.append("private_ollama requires APE_EMBEDDING__BACKEND=ollama")

    if errors:
        joined = "; ".join(errors)
        raise ProductionConfigurationError(f"Invalid production runtime configuration: {joined}")


def _require_secret(
    errors: list[str],
    name: str,
    value: str | None,
    forbidden_values: set[str] | None = None,
) -> None:
    normalized = (value or "").strip()
    if not normalized or normalized in (forbidden_values or set()):
        errors.append(f"{name} must contain a non-default secret")
