"""OpenAI chat provider."""

from __future__ import annotations

from app.platform.providers.implementations.openai_compatible_chat import (
    OpenAICompatibleChatProvider,
)


class OpenAIChatProvider(OpenAICompatibleChatProvider):
    """Chat via OpenAI's API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        provider_version: str,
        base_url: str = "https://api.openai.com",
        request_timeout_seconds: float = 120.0,
    ) -> None:
        super().__init__(
            provider_name="openai",
            api_key=api_key,
            base_url=base_url,
            model=model,
            provider_version=provider_version,
            request_timeout_seconds=request_timeout_seconds,
        )
