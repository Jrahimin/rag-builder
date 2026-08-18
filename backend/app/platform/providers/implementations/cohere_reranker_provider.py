"""Cohere multilingual reranker over HTTP. No vendor SDK."""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.platform.providers.contracts.reranker import (
    BaseRerankerProvider,
    RerankRequest,
    RerankResponse,
    RerankResult,
    RerankScoreScale,
)
from app.platform.providers.errors import (
    ProviderAuthenticationError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class CohereRerankerProvider(BaseRerankerProvider):
    """Cohere v2 /rerank mapped onto the vendor-neutral reranker contract."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.cohere.com",
        model: str = "rerank-v4.0-pro",
        provider_version: str = "1",
        request_timeout_seconds: float = 15.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._provider_version = provider_version
        self._timeout = request_timeout_seconds

    @property
    def provider_name(self) -> str:
        return "cohere"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider_version(self) -> str:
        return self._provider_version

    async def rerank(self, request: RerankRequest) -> RerankResponse:
        if not request.candidates:
            return RerankResponse(
                results=[],
                provider=self.provider_name,
                model=self.model_name,
                provider_version=self.provider_version,
                score_scale=RerankScoreScale.MODEL_RELEVANCE,
            )
        documents = [candidate.text for candidate in request.candidates]
        url = f"{self._base_url}/v2/rerank"
        started = time.perf_counter()
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
                        "query": request.query,
                        "documents": documents,
                        "top_n": min(request.top_n, len(documents)),
                    },
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                "Cohere rerank timed out.",
                provider_name=self.provider_name,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderConnectionError(
                "Cohere rerank request failed.",
                provider_name=self.provider_name,
                context={"http_error_type": type(exc).__name__},
            ) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError(
                "Cohere rerank authentication failed.",
                provider_name=self.provider_name,
            )
        if response.status_code == 429:
            raise ProviderRateLimitError(
                "Cohere rerank rate limited.",
                provider_name=self.provider_name,
            )
        if response.status_code >= 500:
            raise ProviderUnavailableError(
                "Cohere rerank is unavailable.",
                provider_name=self.provider_name,
                context={"http_status": response.status_code},
            )
        if response.is_error:
            raise ProviderError(
                f"Cohere rerank failed (HTTP {response.status_code}).",
                provider_name=self.provider_name,
                context={"http_status": response.status_code},
            )

        payload = response.json()
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise ProviderError(
                "Cohere rerank returned malformed results.",
                provider_name=self.provider_name,
            )
        ranked: list[RerankResult] = []
        seen: set[int] = set()
        for item in raw_results:
            if not isinstance(item, dict):
                raise ProviderError(
                    "Cohere rerank returned a malformed result row.",
                    provider_name=self.provider_name,
                )
            index = item.get("index")
            score = item.get("relevance_score")
            if (
                not isinstance(index, int)
                or index in seen
                or not 0 <= index < len(request.candidates)
            ):
                raise ProviderError(
                    "Cohere rerank returned an invalid document index.",
                    provider_name=self.provider_name,
                )
            if not isinstance(score, (int, float)):
                raise ProviderError(
                    "Cohere rerank returned an invalid relevance score.",
                    provider_name=self.provider_name,
                )
            seen.add(index)
            candidate = request.candidates[index]
            ranked.append(
                RerankResult(
                    chunk_id=candidate.chunk_id,
                    score=float(score),
                    metadata={
                        **candidate.metadata,
                        "rerank_index": index,
                    },
                )
            )
        usage = _usage_dict(payload.get("meta") or payload.get("usage"))
        return RerankResponse(
            results=ranked,
            provider=self.provider_name,
            model=self.model_name,
            provider_version=self.provider_version,
            score_scale=RerankScoreScale.MODEL_RELEVANCE,
            usage=usage,
            latency_ms=latency_ms,
        )


def _usage_dict(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    billed = value.get("billed_units") if isinstance(value.get("billed_units"), dict) else value
    if not isinstance(billed, dict):
        return {}
    usage: dict[str, Any] = {}
    for key in ("search_units", "input_tokens", "output_tokens"):
        raw = billed.get(key)
        if isinstance(raw, (int, float)):
            usage[key] = raw
    return usage
