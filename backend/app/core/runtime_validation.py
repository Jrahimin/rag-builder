"""Fail-fast validation for the two certified production runtime profiles."""

from __future__ import annotations

from app.core.config import (
    EmbeddingBackend,
    EvidenceGateMode,
    JobQueueBackend,
    LLMBackend,
    MalwareScannerBackend,
    OcrBackend,
    RerankerBackend,
    RerankMode,
    ResponseMode,
    RetrievalStrategy,
    RuntimeProfile,
    Settings,
    StorageBackend,
    WebSearchBackend,
)
from app.core.generation_models import validate_generation_model_allowlist


class ProductionConfigurationError(ValueError):
    """Raised before application construction when production settings are unsafe."""


def validate_runtime_config(settings: Settings) -> None:
    """Reject incomplete, internally inconsistent, or unsafe combinations.

    Development and testing retain lightweight providers, but malformed
    infrastructure and provider selections still fail before clients are
    constructed. Production additionally enforces the certified profiles.
    """
    errors = _base_configuration_errors(settings)
    errors.extend(validate_generation_model_allowlist(settings))
    if not settings.app.is_production:
        if errors:
            raise ProductionConfigurationError(
                "Invalid runtime configuration: " + "; ".join(errors)
            )
        return

    profile = _effective_runtime_profile(settings)
    if profile is RuntimeProfile.DEVELOPMENT:
        errors.append(
            "APE_RUNTIME__CAPABILITY_PROFILE_ID must select a production capability profile"
        )
    if settings.embedding.backend is EmbeddingBackend.HASH:
        errors.append("hash embeddings are not allowed in production")
    if settings.llm.backend is LLMBackend.ECHO:
        errors.append("echo chat is not allowed in production")
    if settings.jobs.backend is not JobQueueBackend.TASKIQ:
        errors.append("production requires the taskiq job executor")
    if not settings.jobs.dispatcher_enabled:
        errors.append("production requires the durable outbox dispatcher")
    if not settings.webhooks.enabled or not settings.webhooks.dispatcher_enabled:
        errors.append("production requires the webhook dispatcher")
    if settings.storage.backend is not StorageBackend.MINIO:
        errors.append("production requires MinIO/S3-compatible object storage")
    if settings.malware_scan.backend is not MalwareScannerBackend.CLAMAV:
        errors.append("production requires APE_MALWARE_SCAN__BACKEND=clamav")
    if settings.retrieval.strategy is not RetrievalStrategy.HYBRID:
        errors.append("production requires hybrid retrieval")
    if settings.retrieval.rerank_mode is RerankMode.OFF:
        errors.append("production requires the rerank stage to be enabled")
    if settings.retrieval.reranker_backend is RerankerBackend.NOOP:
        errors.append("production requires a configured reranker backend")
    if settings.chat.evidence_gate_mode is not EvidenceGateMode.ENFORCE:
        errors.append("production requires evidence-gate enforcement")
    if not settings.retrieval.deduplicate_by_content_hash:
        errors.append("production requires content-hash deduplication")
    if not settings.chat.include_citations:
        errors.append("production requires durable citation/provenance creation")
    if (
        settings.ocr.enabled
        and settings.ocr.backend is OcrBackend.NOOP
        and settings.ocr.bangla_backend is OcrBackend.NOOP
    ):
        errors.append("enabled OCR cannot use the noop backend in production")
    if "*" in settings.cors.allow_origins:
        errors.append("production forbids wildcard APE_CORS__ALLOW_ORIGINS")

    _require_secret(errors, "APE_DATABASE__PASSWORD", settings.database.password, {"ape"})
    _require_secret(errors, "APE_REDIS__PASSWORD", settings.redis.password)
    _require_secret(
        errors,
        "APE_WEBHOOKS__SIGNING_KEY",
        settings.webhooks.signing_key,
        {"development-only-webhook-signing-key"},
    )
    _require_secret(errors, "APE_MINIO__ACCESS_KEY", settings.minio.access_key, {"minioadmin"})
    _require_secret(errors, "APE_MINIO__SECRET_KEY", settings.minio.secret_key, {"minioadmin"})
    if OcrBackend.GOOGLE_VISION in {
        settings.ocr.backend,
        settings.ocr.bangla_backend,
    }:
        _require_secret(errors, "APE_OCR__GOOGLE_API_KEY", settings.ocr.google_api_key)

    if not settings.auth.enabled:
        errors.append("production requires organization/admin authentication")
    else:
        _require_secret(errors, "APE_AUTH__ADMIN_JWT_SECRET", settings.auth.admin_jwt_secret)
        _require_secret(errors, "APE_AUTH__KEY_PEPPER", settings.auth.key_pepper)

    if profile is RuntimeProfile.HOSTED_MANAGED:
        if settings.llm.backend is not LLMBackend.OPENAI:
            errors.append("hosted_managed requires APE_LLM__BACKEND=openai")
        if settings.embedding.backend is not EmbeddingBackend.COHERE:
            errors.append("hosted_managed requires APE_EMBEDDING__BACKEND=cohere")
        _require_secret(errors, "APE_LLM__OPENAI_API_KEY", settings.llm.openai_api_key)
        _require_secret(
            errors,
            "APE_COHERE__API_KEY",
            settings.resolved_cohere_api_key() or None,
        )
    elif profile is RuntimeProfile.HOSTED_OPENAI:
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


def _base_configuration_errors(settings: Settings) -> list[str]:
    errors: list[str] = []
    required_values = {
        "APE_DATABASE__HOST": settings.database.host,
        "APE_DATABASE__USER": settings.database.user,
        "APE_DATABASE__PASSWORD": settings.database.password,
        "APE_DATABASE__NAME": settings.database.name,
        "APE_REDIS__HOST": settings.redis.host,
        "APE_EMBEDDING__MODEL": settings.embedding.model,
        "APE_LLM__MODEL": settings.llm.model,
    }
    for name, value in required_values.items():
        if not str(value).strip():
            errors.append(f"{name} is required")

    if settings.storage.backend is StorageBackend.LOCAL:
        if not settings.storage.local_root.strip():
            errors.append("APE_STORAGE__LOCAL_ROOT is required for local storage")
    else:
        for name, value in {
            "APE_MINIO__ENDPOINT": settings.minio.endpoint,
            "APE_MINIO__ACCESS_KEY": settings.minio.access_key,
            "APE_MINIO__SECRET_KEY": settings.minio.secret_key,
            "APE_MINIO__BUCKET": settings.minio.bucket,
        }.items():
            if not value.strip():
                errors.append(f"{name} is required for MinIO storage")

    if settings.jobs.retry_max_delay_seconds < settings.jobs.retry_base_delay_seconds:
        errors.append("jobs.retry_max_delay_seconds must be >= retry_base_delay_seconds")
    if settings.jobs.dispatch_retry_max_seconds < settings.jobs.dispatch_retry_base_seconds:
        errors.append("jobs.dispatch_retry_max_seconds must be >= dispatch_retry_base_seconds")

    webhooks = settings.webhooks
    if webhooks.dispatcher_enabled and not webhooks.enabled:
        errors.append("webhooks.dispatcher_enabled requires webhooks.enabled")
    if webhooks.enabled:
        if len(webhooks.signing_key.encode("utf-8")) < 32:
            errors.append("APE_WEBHOOKS__SIGNING_KEY must be at least 32 bytes")
        if webhooks.retry_max_seconds < webhooks.retry_base_seconds:
            errors.append("webhooks.retry_max_seconds must be >= retry_base_seconds")
        if webhooks.delivery_lease_seconds <= webhooks.delivery_timeout_seconds:
            errors.append("webhooks.delivery_lease_seconds must exceed delivery_timeout_seconds")

    embedding = settings.embedding
    if embedding.backend is EmbeddingBackend.COHERE and not settings.resolved_cohere_api_key():
        errors.append("APE_COHERE__API_KEY is required for Cohere embeddings")
    if (
        settings.retrieval.reranker_backend is RerankerBackend.COHERE
        and not settings.resolved_cohere_api_key()
    ):
        errors.append("APE_COHERE__API_KEY is required for Cohere reranking")
    if embedding.backend is EmbeddingBackend.OPENAI and not embedding.openai_api_key:
        errors.append("APE_EMBEDDING__OPENAI_API_KEY is required for OpenAI embeddings")
    if embedding.backend is EmbeddingBackend.GEMINI and not embedding.gemini_api_key:
        errors.append("APE_EMBEDDING__GEMINI_API_KEY is required for Gemini embeddings")
    fixed_dimensions = {
        (EmbeddingBackend.OPENAI, "text-embedding-ada-002"): 1536,
        (EmbeddingBackend.OLLAMA, "nomic-embed-text"): 768,
    }
    expected_dimensions = fixed_dimensions.get((embedding.backend, embedding.model))
    if expected_dimensions is not None and embedding.dimensions != expected_dimensions:
        errors.append(
            f"{embedding.backend.value}/{embedding.model} requires "
            f"APE_EMBEDDING__DIMENSIONS={expected_dimensions}"
        )

    llm = settings.llm
    if llm.backend is LLMBackend.OPENAI and not llm.openai_api_key:
        errors.append("APE_LLM__OPENAI_API_KEY is required for the OpenAI LLM")
    if llm.backend is LLMBackend.GEMINI and not llm.gemini_api_key:
        errors.append("APE_LLM__GEMINI_API_KEY is required for the Gemini LLM")
    ocr = settings.ocr
    if (
        OcrBackend.GOOGLE_VISION in {ocr.backend, ocr.bangla_backend}
        and not (ocr.google_api_key or "").strip()
    ):
        errors.append("APE_OCR__GOOGLE_API_KEY is required for Google Vision OCR")
    if (
        settings.query_translation.enabled
        and settings.query_translation.backend in {LLMBackend.OPENAI, LLMBackend.OPENAI_COMPATIBLE}
        and not settings.llm.openai_api_key
    ):
        errors.append("APE_LLM__OPENAI_API_KEY is required when query translation uses OpenAI")
    if settings.query_translation.enabled and settings.query_translation.backend is LLMBackend.ECHO:
        errors.append("query translation cannot use the echo LLM backend")
    if (
        settings.chat.response_mode is not ResponseMode.INDEXED_ONLY
        and settings.resolved_web_search_backend() is WebSearchBackend.DISABLED
    ):
        errors.append("web-enabled chat response modes require an OpenAI LLM or web-search backend")
    if (
        settings.resolved_web_search_backend() is WebSearchBackend.OPENAI
        and not settings.resolved_web_search_api_key()
    ):
        errors.append(
            "APE_WEB_SEARCH__OPENAI_API_KEY or APE_LLM__OPENAI_API_KEY is required for web search"
        )
    return errors


def _effective_runtime_profile(settings: Settings) -> RuntimeProfile:
    """Use the canonical capability identity; retain the legacy enum as an alias."""
    if settings.runtime.capability_profile_id is None:
        return settings.runtime.profile
    # Imported lazily because the profile module reuses ProductionConfigurationError.
    from app.core.capability_profiles import deployment_capability_profile

    return deployment_capability_profile(settings).legacy_runtime_alias


def _require_secret(
    errors: list[str],
    name: str,
    value: str | None,
    forbidden_values: set[str] | None = None,
) -> None:
    normalized = (value or "").strip()
    lowered = normalized.lower()
    placeholder = any(
        marker in lowered for marker in ("replace-", "change-me", "change_me", "placeholder")
    )
    if not normalized or normalized in (forbidden_values or set()) or placeholder:
        errors.append(f"{name} must contain a non-default secret")
