"""LLM provider factory.

Single entry point for all chat generation backends. Module and service code
should call :func:`get_llm_provider` or accept :class:`BaseLLMProvider` via DI —
never instantiate OpenAI/Gemini/Ollama classes directly.

See ``docs/learning/conversation_provider_integration.md``.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import LLMBackend, Settings, get_settings
from app.platform.providers.contracts.llm import BaseLLMProvider
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.echo_chat import EchoLLMProvider
from app.platform.providers.implementations.gemini_chat import GeminiChatProvider
from app.platform.providers.implementations.ollama_chat import OllamaChatProvider
from app.platform.providers.implementations.openai_chat import OpenAIChatProvider
from app.platform.providers.implementations.openai_compatible_chat import (
    OpenAICompatibleChatProvider,
)


def _build_llm_provider(
    cfg: Settings,
    *,
    backend: LLMBackend,
    model: str,
) -> BaseLLMProvider:
    llm = cfg.llm
    if backend is LLMBackend.ECHO:
        return EchoLLMProvider(model=model, provider_version=llm.provider_version)
    if backend is LLMBackend.OLLAMA:
        return OllamaChatProvider(
            base_url=llm.ollama_base_url,
            model=model,
            provider_version=llm.provider_version,
            request_timeout_seconds=llm.request_timeout_seconds,
        )
    if backend is LLMBackend.OPENAI:
        if not llm.openai_api_key:
            msg = "OpenAI LLM backend requires APE_LLM__OPENAI_API_KEY"
            raise ProviderError(msg, provider_name="openai")
        return OpenAIChatProvider(
            api_key=llm.openai_api_key,
            base_url=llm.openai_base_url,
            model=model,
            provider_version=llm.provider_version,
            request_timeout_seconds=llm.request_timeout_seconds,
        )
    if backend is LLMBackend.OPENAI_COMPATIBLE:
        if not llm.openai_api_key:
            msg = "OpenAI-compatible LLM backend requires APE_LLM__OPENAI_API_KEY"
            raise ProviderError(msg, provider_name="openai_compatible")
        return OpenAICompatibleChatProvider(
            provider_name="openai_compatible",
            api_key=llm.openai_api_key,
            base_url=llm.openai_base_url,
            model=model,
            provider_version=llm.provider_version,
            request_timeout_seconds=llm.request_timeout_seconds,
        )
    if backend is LLMBackend.GEMINI:
        if not llm.gemini_api_key:
            msg = "Gemini LLM backend requires APE_LLM__GEMINI_API_KEY"
            raise ProviderError(msg, provider_name="gemini")
        return GeminiChatProvider(
            api_key=llm.gemini_api_key,
            base_url=llm.gemini_base_url,
            model=model,
            provider_version=llm.provider_version,
            request_timeout_seconds=llm.request_timeout_seconds,
        )
    msg = f"Unsupported LLM backend: {backend!r}"
    raise ProviderError(msg, provider_name="llm_factory")


def create_llm_provider(
    settings: Settings,
    *,
    backend: LLMBackend | None = None,
    model: str | None = None,
) -> BaseLLMProvider:
    """Build an LLM provider for deployment defaults or per-conversation overrides."""
    cfg = settings.llm
    return _build_llm_provider(
        settings,
        backend=backend or cfg.backend,
        model=model or cfg.model,
    )


def create_llm_provider_for_conversation(
    settings: Settings,
    *,
    provider: str | None,
    model: str | None,
) -> BaseLLMProvider:
    """Resolve the LLM provider from a conversation config snapshot."""
    cfg = settings.llm
    return _build_llm_provider(
        settings,
        backend=LLMBackend(provider or cfg.backend.value),
        model=model or cfg.model,
    )


@lru_cache
def get_llm_provider() -> BaseLLMProvider:
    """Return the process-scoped LLM provider."""
    return create_llm_provider(get_settings())
