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
    assert "30-day refund" in result.evidence[0].content


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
