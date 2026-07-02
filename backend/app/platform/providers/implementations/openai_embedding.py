"""OpenAI embedding provider."""

from __future__ import annotations

from app.platform.providers.implementations.openai_compatible_embedding import (
    OpenAICompatibleEmbeddingProvider,
)


class OpenAIEmbeddingProvider(OpenAICompatibleEmbeddingProvider):
    """Embed texts via OpenAI's embeddings API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        dimensions: int,
        provider_version: str,
        base_url: str = "https://api.openai.com",
    ) -> None:
        super().__init__(
            provider_name="openai",
            api_key=api_key,
            base_url=base_url,
            model=model,
            dimensions=dimensions,
            provider_version=provider_version,
            send_dimensions=True,
        )
