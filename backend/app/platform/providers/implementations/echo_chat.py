"""Deterministic echo LLM provider for tests and local dev."""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.platform.providers.contracts.llm import (
    BaseLLMProvider,
    ChatCompletionChunk,
    ChatCompletionResult,
    ChatMessage,
    ChatRole,
    ChatUsage,
)


class EchoLLMProvider(BaseLLMProvider):
    """Echo the last user message with a short prefix."""

    def __init__(self, *, model: str, provider_version: str) -> None:
        self._model = model
        self._provider_version = provider_version

    @property
    def provider_name(self) -> str:
        return "echo"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider_version(self) -> str:
        return self._provider_version

    def _last_user_content(self, messages: list[ChatMessage]) -> str:
        for message in reversed(messages):
            if message.role is ChatRole.USER:
                return message.content
        return ""

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> ChatCompletionResult:
        del temperature, max_tokens
        user_text = self._last_user_content(messages)
        content = f"[echo] {user_text}"
        return ChatCompletionResult(
            content=content,
            provider=self.provider_name,
            model=self.model_name,
            finish_reason="stop",
            usage=ChatUsage(input_tokens=len(user_text), output_tokens=len(content)),
            provider_version=self._provider_version,
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[ChatCompletionChunk]:
        del temperature, max_tokens
        result = await self.generate(messages, temperature=0.0, max_tokens=1)
        words = result.content.split(" ")
        for index, word in enumerate(words):
            delta = word if index == 0 else f" {word}"
            yield ChatCompletionChunk(delta=delta)
        yield ChatCompletionChunk(delta="", finish_reason="stop")
