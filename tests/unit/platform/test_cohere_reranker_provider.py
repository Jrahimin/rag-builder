"""Cohere reranker contract mapping and failure taxonomy."""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.platform.providers.contracts.reranker import (
    RerankCandidate,
    RerankRequest,
    RerankScoreScale,
)
from app.platform.providers.errors import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from app.platform.providers.implementations.cohere_reranker_provider import CohereRerankerProvider

pytestmark = pytest.mark.unit


def _request() -> RerankRequest:
    return RerankRequest(
        query="source tax",
        candidates=[
            RerankCandidate(chunk_id=uuid.uuid4(), text="wrong nearby tax chunk", source_score=0.2),
            RerankCandidate(chunk_id=uuid.uuid4(), text="উৎসে কর সংগ্রহের খাত", source_score=0.12),
        ],
        top_n=2,
    )


async def test_cohere_maps_relevance_scores_and_indexes(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request()

    class _Response:
        status_code = 200
        is_error = False

        def json(self) -> dict:
            return {
                "results": [
                    {"index": 1, "relevance_score": 0.82},
                    {"index": 0, "relevance_score": 0.11},
                ],
                "meta": {"billed_units": {"search_units": 1}},
            }

    class _Client:
        def __init__(self, timeout: object) -> None:
            del timeout

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, headers: dict, json: dict) -> _Response:
            assert "/v2/rerank" in url
            assert json["model"] == "rerank-v4.0-fast"
            assert json["query"] == "source tax"
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    provider = CohereRerankerProvider(api_key="secret")
    response = await provider.rerank(request)
    assert response.score_scale is RerankScoreScale.MODEL_RELEVANCE
    assert [item.score for item in response.results] == [0.82, 0.11]
    assert response.results[0].chunk_id == request.candidates[1].chunk_id


async def test_cohere_auth_and_rate_limit_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Response:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code
            self.is_error = True

        def json(self) -> dict:
            return {}

    class _Client:
        def __init__(self, timeout: object) -> None:
            del timeout

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, *args: object, **kwargs: object) -> _Response:
            return _Response(self.status)  # type: ignore[attr-defined]

    client_cls = _Client
    monkeypatch.setattr(httpx, "AsyncClient", client_cls)
    provider = CohereRerankerProvider(api_key="secret")
    client_cls.status = 401
    with pytest.raises(ProviderAuthenticationError):
        await provider.rerank(_request())
    client_cls.status = 429
    with pytest.raises(ProviderRateLimitError):
        await provider.rerank(_request())


async def test_cohere_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Client:
        def __init__(self, timeout: object) -> None:
            del timeout

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, *args: object, **kwargs: object) -> None:
            raise httpx.TimeoutException("slow")

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    provider = CohereRerankerProvider(api_key="secret")
    with pytest.raises(ProviderTimeoutError):
        await provider.rerank(_request())
