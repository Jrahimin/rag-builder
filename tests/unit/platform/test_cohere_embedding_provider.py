"""Cohere embed contract mapping for QUERY/DOCUMENT purpose."""

from __future__ import annotations

import httpx
import pytest

from app.platform.providers.contracts.embedding import EmbeddingPurpose
from app.platform.providers.errors import ProviderAuthenticationError
from app.platform.providers.implementations.cohere_embedding import CohereEmbeddingProvider

pytestmark = pytest.mark.unit


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
        def __init__(self, timeout: object) -> None:
            del timeout

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, headers: dict, json: dict) -> _Response:
            assert "/v2/embed" in url
            assert json["model"] == "embed-v4.0"
            assert json["output_dimension"] == 4
            seen.append(json["input_type"])
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
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
        def __init__(self, timeout: object) -> None:
            del timeout

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, headers: dict, json: dict) -> _Response:
            del url, headers, json
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    provider = CohereEmbeddingProvider(api_key="bad", dimensions=4)
    with pytest.raises(ProviderAuthenticationError):
        await provider.embed_texts(["query"], purpose=EmbeddingPurpose.QUERY)
