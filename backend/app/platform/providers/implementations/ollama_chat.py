"""Ollama chat provider."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.platform.providers.contracts.llm import (
    BaseLLMProvider,
    ChatCompletionChunk,
    ChatCompletionResult,
    ChatMessage,
    ChatUsage,
)
from app.platform.providers.errors import ProviderError


class OllamaChatProvider(BaseLLMProvider):
    """Chat via Ollama's /api/chat endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        provider_version: str,
        request_timeout_seconds: float,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._provider_version = provider_version
        self._timeout = request_timeout_seconds

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider_version(self) -> str:
        return self._provider_version

    def _ollama_messages(self, messages: list[ChatMessage]) -> list[dict[str, str]]:
        return [{"role": message.role.value, "content": message.content} for message in messages]

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> ChatCompletionResult:
        del max_tokens
        url = f"{self._base_url}/api/chat"
        body = {
            "model": self._model,
            "messages": self._ollama_messages(messages),
            "stream": False,
            "options": {"temperature": temperature},
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=body)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = "Ollama chat request failed"
            raise ProviderError(msg, provider_name=self.provider_name) from exc

        payload = response.json()
        message = payload.get("message") or {}
        content = str(message.get("content") or "")
        return ChatCompletionResult(
            content=content,
            provider=self.provider_name,
            model=self.model_name,
            finish_reason="stop",
            usage=ChatUsage(
                input_tokens=int(payload.get("prompt_eval_count") or 0),
                output_tokens=int(payload.get("eval_count") or 0),
            ),
            provider_version=self._provider_version,
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[ChatCompletionChunk]:
        del max_tokens
        url = f"{self._base_url}/api/chat"
        body = {
            "model": self._model,
            "messages": self._ollama_messages(messages),
            "stream": True,
            "options": {"temperature": temperature},
        }
        client = httpx.AsyncClient(timeout=self._timeout)
        try:
            async with client.stream("POST", url, json=body) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if payload.get("done"):
                        yield ChatCompletionChunk(delta="", finish_reason="stop")
                        break
                    message = payload.get("message") or {}
                    delta = message.get("content") or ""
                    if delta:
                        yield ChatCompletionChunk(delta=str(delta))
        except httpx.HTTPError as exc:
            msg = "Ollama chat stream failed"
            raise ProviderError(msg, provider_name=self.provider_name) from exc
        finally:
            await client.aclose()
