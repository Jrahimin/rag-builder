"""Query-translation provider factory."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import LLMBackend, Settings, get_settings
from app.platform.providers.contracts.query_translation import BaseQueryTranslationProvider
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.llm_factory import create_llm_provider
from app.platform.providers.implementations.llm_query_translation_provider import (
    LLMQueryTranslationProvider,
)
from app.platform.providers.prompts.retrieval_translation import PROMPT_VERSION


def create_query_translation_provider(
    settings: Settings,
    *,
    backend: LLMBackend | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
) -> BaseQueryTranslationProvider:
    """Build a translator from live credentials and translation-specific model settings."""
    translation = settings.query_translation
    selected_backend = backend or translation.backend
    selected_model = model or translation.model
    if selected_backend is LLMBackend.ECHO:
        raise ProviderError(
            "Echo LLM cannot produce retrieval translations.",
            provider_name="query_translation",
        )
    llm = create_llm_provider(
        settings,
        backend=selected_backend,
        model=selected_model,
        request_timeout_seconds=translation.request_timeout_seconds,
    )
    return LLMQueryTranslationProvider(
        llm,
        prompt_version=prompt_version or translation.prompt_version or PROMPT_VERSION,
        max_output_tokens=translation.max_output_tokens,
        temperature=None,
        retry_max_attempts=translation.retry_max_attempts,
    )


@lru_cache
def get_query_translation_provider() -> BaseQueryTranslationProvider:
    return create_query_translation_provider(get_settings())
