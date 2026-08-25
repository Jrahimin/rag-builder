"""Factory for the configured external web-search provider."""

from __future__ import annotations

from app.core.config import Settings, WebSearchBackend
from app.platform.providers.contracts.web_search import BaseWebSearchProvider
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.openai_web_search import OpenAIWebSearchProvider


def create_web_search_provider(settings: Settings) -> BaseWebSearchProvider:
    config = settings.web_search
    if config.backend is WebSearchBackend.DISABLED:
        raise ProviderError("Web search is disabled", provider_name="web_search")
    if config.backend is WebSearchBackend.OPENAI:
        api_key = settings.resolved_web_search_api_key()
        if not api_key:
            raise ProviderError(
                "OpenAI web search requires APE_WEB_SEARCH__OPENAI_API_KEY or "
                "APE_LLM__OPENAI_API_KEY",
                provider_name="openai",
            )
        return OpenAIWebSearchProvider(
            api_key=api_key,
            base_url=settings.resolved_web_search_base_url(),
            model=config.model,
            provider_version=config.provider_version,
            request_timeout_seconds=config.request_timeout_seconds,
            max_output_tokens=config.max_output_tokens,
            max_evidence_chars=config.max_evidence_chars,
        )
    raise ProviderError(
        f"Unsupported web-search backend: {config.backend!r}",
        provider_name="web_search",
    )
