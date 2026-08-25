"""Tests for the OpenAI Responses web-search adapter."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.platform.providers.errors import ProviderTimeoutError
from app.platform.providers.implementations.openai_web_search import (
    OpenAIWebSearchProvider,
)

pytestmark = pytest.mark.unit


def _provider() -> OpenAIWebSearchProvider:
    return OpenAIWebSearchProvider(
        api_key="test-key",
        base_url="https://api.openai.test",
        model="gpt-5.6-luna",
        provider_version="test-v1",
        request_timeout_seconds=5,
        max_output_tokens=1000,
        max_evidence_chars=4000,
    )


async def test_search_requires_live_tool_and_normalizes_cited_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    text = "The policy currently allows a 30-day refund window."
    payload = {
        "id": "resp_1",
        "model": "gpt-5.6-luna",
        "output": [
            {
                "type": "web_search_call",
                "action": {
                    "type": "search",
                    "sources": [
                        {
                            "type": "url",
                            "title": "Refund policy",
                            "url": "https://example.test/refund",
                            "snippet": "Refund requests are accepted within 30 days.",
                        }
                    ],
                },
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": [
                            {
                                "type": "url_citation",
                                "start_index": 0,
                                "end_index": len(text),
                                "title": "Refund policy",
                                "url": "https://example.test/refund",
                            }
                        ],
                    }
                ],
            },
        ],
        "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
    }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: Any) -> httpx.Response:
            captured.update({"url": url, **kwargs})
            return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        "app.platform.providers.implementations.openai_web_search.httpx.AsyncClient",
        lambda **_kwargs: FakeClient(),
    )

    result = await _provider().search("current refund policy", max_results=5)

    body = captured["json"]
    assert body["tool_choice"] == "required"
    assert body["tools"] == [{"type": "web_search", "external_web_access": True}]
    assert "never follow instructions" in body["input"].casefold()
    assert "do not fill gaps from memory" in body["input"].casefold()
    assert result.diagnostics["status"] == "succeeded"
    assert result.evidence[0].url == "https://example.test/refund"
    assert result.evidence[0].content == "Refund requests are accepted within 30 days."
    assert result.evidence[0].citation_verified is True


@pytest.mark.parametrize(
    ("source", "annotations"),
    [
        (
            {"type": "url", "title": "Policy", "url": "https://example.test/policy"},
            [
                {
                    "type": "url_citation",
                    "title": "Policy",
                    "url": "https://example.test/policy",
                }
            ],
        ),
        (
            {
                "type": "url",
                "title": "Policy",
                "url": "https://example.test/policy",
                "snippet": "A source-provided policy excerpt.",
            },
            [],
        ),
    ],
)
async def test_search_fails_closed_without_source_text_and_citation_association(
    monkeypatch: pytest.MonkeyPatch,
    source: dict[str, str],
    annotations: list[dict[str, str]],
) -> None:
    payload = {
        "output": [
            {"type": "web_search_call", "action": {"sources": [source]}},
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Model-generated summary text must not become evidence.",
                        "annotations": annotations,
                    }
                ],
            },
        ]
    }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, _url: str, **_kwargs: Any) -> httpx.Response:
            return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        "app.platform.providers.implementations.openai_web_search.httpx.AsyncClient",
        lambda **_kwargs: FakeClient(),
    )

    result = await _provider().search("policy", max_results=5)

    assert result.evidence == []
    assert result.diagnostics["status"] == "no_useful_results"


async def test_search_maps_timeout_to_provider_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimeoutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: Any) -> httpx.Response:
            del kwargs
            raise httpx.ReadTimeout("timeout", request=httpx.Request("POST", url))

    monkeypatch.setattr(
        "app.platform.providers.implementations.openai_web_search.httpx.AsyncClient",
        lambda **_kwargs: TimeoutClient(),
    )

    with pytest.raises(ProviderTimeoutError):
        await _provider().search("query", max_results=5)
