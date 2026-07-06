"""Unit tests for echo LLM provider and factory."""

from __future__ import annotations

import pytest

from app.core.config import LLMBackend, LLMConfig, Settings
from app.platform.providers.contracts.llm import ChatMessage, ChatRole
from app.platform.providers.implementations.echo_chat import EchoLLMProvider
from app.platform.providers.implementations.llm_factory import create_llm_provider

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


def test_factory_echo_backend() -> None:
    settings = Settings(llm=LLMConfig(backend=LLMBackend.ECHO))
    provider = create_llm_provider(settings)
    assert provider.provider_name == "echo"


def test_factory_conversation_override_model() -> None:
    settings = Settings(llm=LLMConfig(backend=LLMBackend.ECHO, model="default-model"))
    provider = create_llm_provider(settings, model="override-model")
    assert provider.model_name == "override-model"
