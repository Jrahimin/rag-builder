"""Gemini chat provider."""

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

_GEMINI_ROLE = {
    ChatRole.USER: "user",
    ChatRole.ASSISTANT: "model",
}


class GeminiChatProvider(BaseLLMProvider):
    """Chat via Gemini generateContent API."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        provider_version: str,
        request_timeout_seconds: float,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._provider_version = provider_version
        self._timeout = request_timeout_seconds

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider_version(self) -> str:
        return self._provider_version

    def _split_messages(
        self, messages: list[ChatMessage]
    ) -> tuple[str | None, list[dict[str, object]]]:
        system_parts: list[str] = []
        contents: list[dict[str, object]] = []
        for message in messages:
            if message.role is ChatRole.SYSTEM:
                system_parts.append(message.content)
                continue
            contents.append(
                {
                    "role": _GEMINI_ROLE[message.role],
                    "parts": [{"text": message.content}],
                }
            )
        system_instruction = "\n\n".join(system_parts) if system_parts else None
        return system_instruction, contents

    def _request_body(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, object]:
        system_instruction, contents = self._split_messages(messages)
        body: dict[str, object] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_instruction:
            body["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        return body

    def _url(self, *, stream: bool) -> str:
        action = "streamGenerateContent" if stream else "generateContent"
        return f"{self._base_url}/models/{self._model}:{action}?key={self._api_key}"

    def _parse_response(self, payload: dict[str, object]) -> ChatCompletionResult:
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            msg = "Gemini returned an invalid chat payload"
            raise ProviderError(msg, provider_name=self.provider_name)

        candidate = candidates[0]
        if not isinstance(candidate, dict):
            msg = "Gemini returned an invalid chat payload"
            raise ProviderError(msg, provider_name=self.provider_name)

        content_obj = candidate.get("content") or {}
        parts = content_obj.get("parts") if isinstance(content_obj, dict) else []
        text_parts: list[str] = []
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict) and part.get("text"):
                    text_parts.append(str(part["text"]))
        content = "".join(text_parts)
        usage_meta = payload.get("usageMetadata") or {}
        if not isinstance(usage_meta, dict):
            usage_meta = {}
        return ChatCompletionResult(
            content=content,
            provider=self.provider_name,
            model=self.model_name,
            finish_reason=str(candidate.get("finishReason") or "stop"),
            usage=ChatUsage(
                input_tokens=int(usage_meta.get("promptTokenCount") or 0),
                output_tokens=int(usage_meta.get("candidatesTokenCount") or 0),
            ),
            provider_version=self._provider_version,
        )

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> ChatCompletionResult:
        url = self._url(stream=False)
        body = self._request_body(messages, temperature=temperature, max_tokens=max_tokens)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=body)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = "Gemini chat request failed"
            raise ProviderError(msg, provider_name=self.provider_name) from exc

        payload = response.json()
        if not isinstance(payload, dict):
            msg = "Gemini returned an invalid chat payload"
            raise ProviderError(msg, provider_name=self.provider_name)
        return self._parse_response(payload)

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[ChatCompletionChunk]:
        url = self._url(stream=True)
        body = self._request_body(messages, temperature=temperature, max_tokens=max_tokens)
        client = httpx.AsyncClient(timeout=self._timeout)
        emitted_length = 0
        try:
            async with client.stream("POST", url, json=body) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    stripped = line.strip().rstrip(",")
                    if not stripped or stripped in {"[", "]"}:
                        continue
                    try:
                        payload = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    result = self._parse_response(payload)
                    if result.content:
                        delta = result.content[emitted_length:]
                        emitted_length = len(result.content)
                        if delta:
                            yield ChatCompletionChunk(delta=delta)
                    if result.finish_reason:
                        yield ChatCompletionChunk(delta="", finish_reason=result.finish_reason)
        except httpx.HTTPError as exc:
            msg = "Gemini chat stream failed"
            raise ProviderError(msg, provider_name=self.provider_name) from exc
        finally:
            await client.aclose()
