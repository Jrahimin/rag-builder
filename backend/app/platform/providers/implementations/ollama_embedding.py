"""Ollama embedding provider."""

from __future__ import annotations

import httpx

from app.platform.providers.contracts.embedding import (
    BaseEmbeddingProvider,
    EmbeddingBatchResult,
    coerce_embedding_vector,
)
from app.platform.providers.errors import ProviderError


class OllamaEmbeddingProvider(BaseEmbeddingProvider):
    """Embed texts via Ollama's /api/embeddings endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        dimensions: int,
        provider_version: str,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dimensions = dimensions
        self._provider_version = provider_version

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def provider_version(self) -> str:
        return self._provider_version

    async def embed_texts(self, texts: list[str]) -> EmbeddingBatchResult:
        vectors: list[list[float]] = []
        async with httpx.AsyncClient(timeout=120.0) as client:
            for text in texts:
                try:
                    response = await client.post(
                        f"{self._base_url}/api/embeddings",
                        json={"model": self._model, "prompt": text},
                    )
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    msg = "Ollama embedding request failed"
                    raise ProviderError(msg, provider_name=self.provider_name) from exc

                payload = response.json()
                vectors.append(
                    coerce_embedding_vector(
                        payload.get("embedding"),
                        dimensions=self._dimensions,
                        provider_name=self.provider_name,
                    )
                )

        return EmbeddingBatchResult(
            vectors=vectors,
            provider=self.provider_name,
            model=self.model_name,
            dimensions=self._dimensions,
            provider_version=self._provider_version,
        )
