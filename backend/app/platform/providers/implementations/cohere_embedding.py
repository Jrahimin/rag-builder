"""Cohere multilingual embeddings over HTTP. No vendor SDK."""

from __future__ import annotations

from typing import Any

import httpx

from app.platform.providers.contracts.embedding import (
    BaseEmbeddingProvider,
    EmbeddingBatchResult,
    EmbeddingPurpose,
    coerce_embedding_vector,
)
from app.platform.providers.errors import (
    ProviderAuthenticationError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

_PURPOSE_TO_INPUT_TYPE = {
    EmbeddingPurpose.QUERY: "search_query",
    EmbeddingPurpose.DOCUMENT: "search_document",
}


class CohereEmbeddingProvider(BaseEmbeddingProvider):
    """Cohere v2 /embed mapped onto the vendor-neutral embedding contract."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "embed-v4.0",
        dimensions: int = 1024,
        provider_version: str = "1",
        base_url: str = "https://api.cohere.com",
        request_timeout_seconds: float = 120.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions
        self._provider_version = provider_version
        self._base_url = base_url.rstrip("/")
        self._timeout = request_timeout_seconds

    @property
    def provider_name(self) -> str:
        return "cohere"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def provider_version(self) -> str:
        return self._provider_version

    async def embed_texts(
        self,
        texts: list[str],
        *,
        purpose: EmbeddingPurpose = EmbeddingPurpose.DOCUMENT,
    ) -> EmbeddingBatchResult:
        if not texts:
            return EmbeddingBatchResult(
                vectors=[],
                provider=self.provider_name,
                model=self.model_name,
                dimensions=self._dimensions,
                provider_version=self._provider_version,
            )
        url = f"{self._base_url}/v2/embed"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "texts": texts,
                        "input_type": _PURPOSE_TO_INPUT_TYPE[purpose],
                        "embedding_types": ["float"],
                        "output_dimension": self._dimensions,
                    },
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                "Cohere embed timed out.",
                provider_name=self.provider_name,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderConnectionError(
                "Cohere embed request failed.",
                provider_name=self.provider_name,
                context={"http_error_type": type(exc).__name__},
            ) from exc

        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError(
                "Cohere embed authentication failed.",
                provider_name=self.provider_name,
            )
        if response.status_code == 429:
            raise ProviderRateLimitError(
                "Cohere embed rate limited.",
                provider_name=self.provider_name,
            )
        if response.status_code >= 500:
            raise ProviderUnavailableError(
                "Cohere embed is unavailable.",
                provider_name=self.provider_name,
                context={"http_status": response.status_code},
            )
        if response.is_error:
            raise ProviderError(
                f"Cohere embed failed (HTTP {response.status_code}).",
                provider_name=self.provider_name,
                context={"http_status": response.status_code},
            )

        payload = response.json()
        vectors = _float_vectors(payload, dimensions=self._dimensions)
        if len(vectors) != len(texts):
            raise ProviderError(
                "Cohere embed returned a mismatched vector batch.",
                provider_name=self.provider_name,
            )
        return EmbeddingBatchResult(
            vectors=vectors,
            provider=self.provider_name,
            model=self.model_name,
            dimensions=self._dimensions,
            provider_version=self._provider_version,
        )


def _float_vectors(payload: object, *, dimensions: int) -> list[list[float]]:
    if not isinstance(payload, dict):
        raise ProviderError(
            "Cohere embed returned an invalid payload.",
            provider_name="cohere",
        )
    embeddings = payload.get("embeddings")
    raw_rows = embeddings.get("float") if isinstance(embeddings, dict) else embeddings
    if not isinstance(raw_rows, list):
        raise ProviderError(
            "Cohere embed returned malformed embeddings.",
            provider_name="cohere",
        )
    return [
        coerce_embedding_vector(row, dimensions=dimensions, provider_name="cohere")
        for row in _unwrap_rows(raw_rows)
    ]


def _unwrap_rows(rows: list[Any]) -> list[Any]:
    unwrapped: list[Any] = []
    for row in rows:
        if isinstance(row, dict) and "embedding" in row:
            unwrapped.append(row.get("embedding"))
        else:
            unwrapped.append(row)
    return unwrapped
