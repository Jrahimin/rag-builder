"""Unit tests for echo LLM provider and factory."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import LLMBackend, LLMConfig, Settings
from app.platform.providers.contracts.llm import ChatMessage, ChatRole
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.echo_chat import EchoLLMProvider
from app.platform.providers.implementations.gemini_chat import _gemini_stream_chunk
from app.platform.providers.implementations.llm_factory import create_llm_provider
from app.platform.providers.implementations.ollama_chat import _context_window_from_show
from app.platform.providers.implementations.openai_chat import OpenAIChatProvider
from app.platform.providers.implementations.openai_compatible_chat import (
    OpenAICompatibleChatProvider,
)

pytestmark = pytest.mark.unit


async def test_echo_generate_prefixes_user_message() -> None:
    provider = EchoLLMProvider(model="echo-model", provider_version="1")
    result = await provider.generate(
        [ChatMessage(role=ChatRole.USER, content="hello")],
        temperature=0.0,
        max_tokens=10,
    )
    assert result.content == "[echo] hello"
    assert result.provider == "echo"


async def test_echo_stream_yields_tokens() -> None:
    provider = EchoLLMProvider(model="echo-model", provider_version="1")
    chunks = [
        chunk
        async for chunk in provider.stream(
            [ChatMessage(role=ChatRole.USER, content="hi there")],
            temperature=0.0,
            max_tokens=10,
        )
    ]
    assert "".join(chunk.delta for chunk in chunks if chunk.delta) == "[echo] hi there"
    assert chunks[-1].usage is not None


def test_gemini_stream_preserves_incremental_text_and_final_usage() -> None:
    first = _gemini_stream_chunk({"candidates": [{"content": {"parts": [{"text": "first "}]}}]})
    final = _gemini_stream_chunk(
        {
            "candidates": [{"content": {"parts": [{"text": "second"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2},
        }
    )

    assert first is not None and first.delta == "first "
    assert first.finish_reason is None
    assert final is not None and final.delta == "second"
    assert final.finish_reason == "STOP"
    assert final.usage is not None
    assert final.usage.input_tokens == 7
    assert final.usage.output_tokens == 2


def test_factory_echo_backend() -> None:
    settings = Settings(llm=LLMConfig(backend=LLMBackend.ECHO))
    provider = create_llm_provider(settings)
    assert provider.provider_name == "echo"


def test_factory_conversation_override_model() -> None:
    settings = Settings(llm=LLMConfig(backend=LLMBackend.ECHO, model="default-model"))
    provider = create_llm_provider(settings, model="override-model")
    assert provider.model_name == "override-model"


def test_openai_unset_temperature_is_omitted() -> None:
    provider = OpenAIChatProvider(
        api_key="test-key",
        model="any-openai-model",
        provider_version="test",
    )

    body = provider._body(
        [ChatMessage(role=ChatRole.USER, content="hello")],
        temperature=None,
        max_tokens=1,
        stream=False,
    )

    assert body["max_completion_tokens"] == 1
    assert "temperature" not in body


def test_openai_chat_model_keeps_temperature() -> None:
    provider = OpenAIChatProvider(
        api_key="test-key",
        model="gpt-4o-mini",
        provider_version="test",
    )

    body = provider._body(
        [ChatMessage(role=ChatRole.USER, content="hello")],
        temperature=0.7,
        max_tokens=32,
        stream=False,
    )

    assert body["max_completion_tokens"] == 32
    assert body["temperature"] == 0.7


def test_gpt5_nano_requests_low_reasoning_effort_for_page_length_budgets() -> None:
    provider = OpenAIChatProvider(
        api_key="test-key",
        model="gpt-5-nano",
        provider_version="test",
    )
    body = provider._body(
        [ChatMessage(role=ChatRole.USER, content="source tax")],
        temperature=None,
        max_tokens=2048,
        stream=False,
    )
    assert body["max_completion_tokens"] == 2048
    assert body["reasoning_effort"] == "low"
    assert "temperature" not in body


def test_non_reasoning_models_do_not_send_reasoning_effort() -> None:
    provider = OpenAIChatProvider(
        api_key="test-key",
        model="gpt-4o-mini",
        provider_version="test",
    )
    body = provider._body(
        [ChatMessage(role=ChatRole.USER, content="hello")],
        temperature=0.2,
        max_tokens=32,
        stream=False,
    )
    assert "reasoning_effort" not in body


def test_stream_usage_is_only_sent_when_provider_capability_allows_it() -> None:
    openai = OpenAIChatProvider(
        api_key="test-key",
        model="gpt-4o-mini",
        provider_version="test",
    )
    compatible = OpenAICompatibleChatProvider(
        provider_name="openai_compatible",
        api_key="test-key",
        base_url="https://compatible.invalid",
        model="custom-model",
        provider_version="test",
        request_timeout_seconds=1,
    )
    messages = [ChatMessage(role=ChatRole.USER, content="hello")]

    assert openai._body(messages, max_tokens=10, stream=True)["stream_options"] == {
        "include_usage": True
    }
    assert "stream_options" not in compatible._body(messages, max_tokens=10, stream=True)


def test_ollama_context_uses_reported_model_limit_and_lower_num_ctx() -> None:
    payload = {
        "model_info": {"llama.context_length": 131072},
        "parameters": "temperature 0.7\nnum_ctx 8192",
    }

    assert _context_window_from_show(payload) == 8192


async def test_openai_error_exposes_only_safe_upstream_diagnostics(monkeypatch) -> None:
    class FailingClient:
        async def __aenter__(self) -> FailingClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, *_args: object, **_kwargs: object) -> httpx.Response:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Unsupported parameter: 'temperature'.",
                        "type": "invalid_request_error",
                        "param": "temperature",
                        "code": "unsupported_parameter",
                    }
                },
            )

    client = FailingClient()
    monkeypatch.setattr(
        "app.platform.providers.implementations.openai_compatible_chat.httpx.AsyncClient",
        lambda **_kwargs: client,
    )
    provider = OpenAIChatProvider(
        api_key="test-key-that-must-not-leak",
        model="test-model",
        provider_version="test",
    )

    with pytest.raises(ProviderError) as caught:
        await provider.generate(
            [ChatMessage(role=ChatRole.USER, content="private prompt")],
            max_tokens=20,
        )

    assert str(caught.value) == "openai chat request failed (HTTP 400)"
    assert caught.value.context == {
        "http_status": 400,
        "error_message": "Unsupported parameter: 'temperature'.",
        "error_type": "invalid_request_error",
        "error_param": "temperature",
        "error_code": "unsupported_parameter",
    }
    assert "test-key-that-must-not-leak" not in str(caught.value.context)
    assert "private prompt" not in str(caught.value.context)


async def test_openai_generate_reads_content_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    class SuccessClient:
        async def __aenter__(self) -> SuccessClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, *_args: object, **_kwargs: object) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {"type": "text", "text": "উৎসে কর"},
                                ],
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 3},
                },
            )

    client = SuccessClient()
    monkeypatch.setattr(
        "app.platform.providers.implementations.openai_compatible_chat.httpx.AsyncClient",
        lambda **_kwargs: client,
    )
    provider = OpenAIChatProvider(
        api_key="test-key",
        model="gpt-5-nano",
        provider_version="test",
    )
    result = await provider.generate(
        [ChatMessage(role=ChatRole.USER, content="source tax")],
        max_tokens=32,
    )
    assert result.content == "উৎসে কর"
