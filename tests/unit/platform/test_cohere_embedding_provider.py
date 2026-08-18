"""Cohere embed contract mapping for QUERY/DOCUMENT purpose."""

from __future__ import annotations

import httpx
import pytest

from app.platform.providers.contracts.embedding import EmbeddingPurpose
from app.platform.providers.errors import ProviderAuthenticationError
from app.platform.providers.implementations import cohere_http
from app.platform.providers.implementations.cohere_embedding import CohereEmbeddingProvider
from app.platform.providers.implementations.cohere_http import (
    clear_shared_cohere_clients,
    shared_cohere_client,
)

pytestmark = pytest.mark.unit


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    monkeypatch.setattr(cohere_http, "shared_cohere_client", lambda **kwargs: client)


async def test_cohere_maps_query_and_document_input_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    class _Response:
        status_code = 200
        is_error = False

        def json(self) -> dict:
            return {"embeddings": {"float": [[0.1] * 4]}}

    class _Client:
        async def post(self, url: str, headers: dict, json: dict) -> _Response:
            assert "/v2/embed" in url
            assert json["model"] == "embed-v4.0"
            assert json["output_dimension"] == 4
            seen.append(json["input_type"])
            return _Response()

    _patch_client(monkeypatch, _Client())
    provider = CohereEmbeddingProvider(api_key="secret", dimensions=4)
    await provider.embed_texts(["query"], purpose=EmbeddingPurpose.QUERY)
    await provider.embed_texts(["passage"], purpose=EmbeddingPurpose.DOCUMENT)
    assert seen == ["search_query", "search_document"]


async def test_cohere_embed_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Response:
        status_code = 401
        is_error = True

        def json(self) -> dict:
            return {}

    class _Client:
        async def post(self, url: str, headers: dict, json: dict) -> _Response:
            del url, headers, json
            return _Response()

    _patch_client(monkeypatch, _Client())
    provider = CohereEmbeddingProvider(api_key="bad", dimensions=4)
    with pytest.raises(ProviderAuthenticationError):
        await provider.embed_texts(["query"], purpose=EmbeddingPurpose.QUERY)


async def test_shared_cohere_clients_reuse_same_timeout_and_split_embed_rerank() -> None:
    clear_shared_cohere_clients()
    rerank = shared_cohere_client(base_url="https://api.cohere.com", timeout=15.0)
    rerank_again = shared_cohere_client(base_url="https://api.cohere.com", timeout=15.0)
    embed = shared_cohere_client(base_url="https://api.cohere.com", timeout=120.0)
    try:
        assert rerank is rerank_again
        assert embed is not rerank
    finally:
        await rerank.aclose()
        await embed.aclose()
        clear_shared_cohere_clients()


async def test_aclose_shared_cohere_clients_closes_cached_clients() -> None:
    clear_shared_cohere_clients()
    first = shared_cohere_client(base_url="https://api.cohere.com", timeout=15.0)
    second = shared_cohere_client(base_url="https://api.cohere.com", timeout=120.0)
    await cohere_http.aclose_shared_cohere_clients()
    assert first.is_closed
    assert second.is_closed
    reused = shared_cohere_client(base_url="https://api.cohere.com", timeout=15.0)
    try:
        assert reused is not first
        assert not reused.is_closed
    finally:
        await cohere_http.aclose_shared_cohere_clients()


async def test_cohere_embed_timeout_uses_httpx_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Client:
        async def post(self, url: str, headers: dict, json: dict) -> None:
            del url, headers, json
            raise httpx.TimeoutException("slow")

    _patch_client(monkeypatch, _Client())
    provider = CohereEmbeddingProvider(api_key="secret", dimensions=4)
    from app.platform.providers.errors import ProviderTimeoutError

    with pytest.raises(ProviderTimeoutError):
        await provider.embed_texts(["query"], purpose=EmbeddingPurpose.QUERY)
