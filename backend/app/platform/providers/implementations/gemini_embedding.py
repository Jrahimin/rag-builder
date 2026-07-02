"""Google Gemini embedding provider."""

from __future__ import annotations

import httpx

from app.platform.providers.contracts.embedding import (
    BaseEmbeddingProvider,
    EmbeddingBatchResult,
    coerce_embedding_vector,
)
from app.platform.providers.errors import ProviderError


class GeminiEmbeddingProvider(BaseEmbeddingProvider):
    """Embed texts via Gemini ``batchEmbedContents`` API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        dimensions: int,
        provider_version: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
    ) -> None:
        self._api_key = api_key
        self._model = model if model.startswith("models/") else f"models/{model}"
        self._dimensions = dimensions
        self._provider_version = provider_version
        self._base_url = base_url.rstrip("/")

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model.removeprefix("models/")

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def provider_version(self) -> str:
        return self._provider_version

    async def embed_texts(self, texts: list[str]) -> EmbeddingBatchResult:
        if not texts:
            return EmbeddingBatchResult(
                vectors=[],
                provider=self.provider_name,
                model=self.model_name,
                dimensions=self._dimensions,
                provider_version=self._provider_version,
            )

        requests = [
            {
                "model": self._model,
                "content": {"parts": [{"text": text}]},
                "outputDimensionality": self._dimensions,
            }
            for text in texts
        ]
        url = f"{self._base_url}/{self._model}:batchEmbedContents"
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    url,
                    params={"key": self._api_key},
                    json={"requests": requests},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = "Gemini embedding request failed"
            raise ProviderError(msg, provider_name=self.provider_name) from exc

        payload = response.json()
        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list):
            msg = "Gemini returned an invalid embedding payload"
            raise ProviderError(msg, provider_name=self.provider_name)

        vectors = [
            coerce_embedding_vector(
                row.get("values"),
                dimensions=self._dimensions,
                provider_name=self.provider_name,
            )
            for row in embeddings
        ]

        return EmbeddingBatchResult(
            vectors=vectors,
            provider=self.provider_name,
            model=self.model_name,
            dimensions=self._dimensions,
            provider_version=self._provider_version,
        )
