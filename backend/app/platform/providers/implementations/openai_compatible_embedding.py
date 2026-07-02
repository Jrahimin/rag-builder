"""OpenAI-compatible embeddings API client (OpenAI, vLLM, LiteLLM, etc.)."""

from __future__ import annotations

import httpx

from app.platform.providers.contracts.embedding import (
    BaseEmbeddingProvider,
    EmbeddingBatchResult,
    coerce_embedding_vector,
)
from app.platform.providers.errors import ProviderError


class OpenAICompatibleEmbeddingProvider(BaseEmbeddingProvider):
    """Embed texts via an OpenAI-compatible ``/v1/embeddings`` endpoint."""

    def __init__(
        self,
        *,
        provider_name: str,
        api_key: str,
        base_url: str,
        model: str,
        dimensions: int,
        provider_version: str,
        send_dimensions: bool = True,
    ) -> None:
        self._provider_name = provider_name
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dimensions = dimensions
        self._provider_version = provider_version
        self._send_dimensions = send_dimensions

    @property
    def provider_name(self) -> str:
        return self._provider_name

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
        if not texts:
            return EmbeddingBatchResult(
                vectors=[],
                provider=self.provider_name,
                model=self.model_name,
                dimensions=self._dimensions,
                provider_version=self._provider_version,
            )

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, object] = {"model": self._model, "input": texts}
        if self._send_dimensions and self._dimensions:
            body["dimensions"] = self._dimensions

        url = f"{self._base_url}/v1/embeddings"
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(url, headers=headers, json=body)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = f"{self.provider_name} embedding request failed"
            raise ProviderError(msg, provider_name=self.provider_name) from exc

        payload = response.json()
        data = payload.get("data")
        if not isinstance(data, list):
            msg = f"{self.provider_name} returned an invalid embedding payload"
            raise ProviderError(msg, provider_name=self.provider_name)

        sorted_rows = sorted(data, key=lambda row: row.get("index", 0))
        vectors = [
            coerce_embedding_vector(
                row.get("embedding"),
                dimensions=self._dimensions,
                provider_name=self.provider_name,
            )
            for row in sorted_rows
        ]

        return EmbeddingBatchResult(
            vectors=vectors,
            provider=self.provider_name,
            model=self.model_name,
            dimensions=self._dimensions,
            provider_version=self._provider_version,
        )
