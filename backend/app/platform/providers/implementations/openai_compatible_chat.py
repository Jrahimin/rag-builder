"""OpenAI-compatible chat completions client (OpenAI, vLLM, LiteLLM, etc.)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.platform.providers.contracts.llm import (
    BaseLLMProvider,
    ChatCompletionChunk,
    ChatCompletionResult,
    ChatMessage,
    ChatRole,
    ChatUsage,
)
from app.platform.providers.errors import ProviderError


def _role_value(role: ChatRole) -> str:
    return role.value


class OpenAICompatibleChatProvider(BaseLLMProvider):
    """Chat via an OpenAI-compatible ``/v1/chat/completions`` endpoint."""

    def __init__(
        self,
        *,
        provider_name: str,
        api_key: str,
        base_url: str,
        model: str,
        provider_version: str,
        request_timeout_seconds: float,
    ) -> None:
        self._provider_name = provider_name
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._provider_version = provider_version
        self._timeout = request_timeout_seconds

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider_version(self) -> str:
        return self._provider_version

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _body(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
        stream: bool,
    ) -> dict[str, object]:
        return {
            "model": self._model,
            "messages": [{"role": _role_value(m.role), "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> ChatCompletionResult:
        url = f"{self._base_url}/v1/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    url,
                    headers=self._headers(),
                    json=self._body(
                        messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        stream=False,
                    ),
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = f"{self.provider_name} chat request failed"
            raise ProviderError(msg, provider_name=self.provider_name) from exc

        payload = response.json()
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            msg = f"{self.provider_name} returned an invalid chat payload"
            raise ProviderError(msg, provider_name=self.provider_name)

        choice = choices[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        finish_reason = choice.get("finish_reason")
        usage_raw = payload.get("usage") or {}
        usage = ChatUsage(
            input_tokens=int(usage_raw.get("prompt_tokens") or 0),
            output_tokens=int(usage_raw.get("completion_tokens") or 0),
        )
        return ChatCompletionResult(
            content=str(content),
            provider=self.provider_name,
            model=self.model_name,
            finish_reason=str(finish_reason) if finish_reason else None,
            usage=usage,
            provider_version=self._provider_version,
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[ChatCompletionChunk]:
        url = f"{self._base_url}/v1/chat/completions"
        client = httpx.AsyncClient(timeout=self._timeout)
        try:
            async with client.stream(
                "POST",
                url,
                headers=self._headers(),
                json=self._body(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                ),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = payload.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    delta_obj = choices[0].get("delta") or {}
                    delta = delta_obj.get("content") or ""
                    finish_reason = choices[0].get("finish_reason")
                    if delta or finish_reason:
                        yield ChatCompletionChunk(
                            delta=str(delta),
                            finish_reason=str(finish_reason) if finish_reason else None,
                        )
        except httpx.HTTPError as exc:
            msg = f"{self.provider_name} chat stream failed"
            raise ProviderError(msg, provider_name=self.provider_name) from exc
        finally:
            await client.aclose()
