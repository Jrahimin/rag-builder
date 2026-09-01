"""Gemini chat provider."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.platform.providers.capabilities import (
    describe_llm_capability,
    translate_generation_parameters,
)
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
        temperature: float | None = None,
        max_tokens: int,
    ) -> dict[str, object]:
        system_instruction, contents = self._split_messages(messages)
        generation_config: dict[str, object] = {}
        generation_config.update(
            translate_generation_parameters(
                describe_llm_capability(self.provider_name, self.model_name),
                temperature=temperature,
                max_tokens=max_tokens,
            )
        )
        body: dict[str, object] = {
            "contents": contents,
            "generationConfig": generation_config,
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
                input_tokens=(
                    int(usage_meta["promptTokenCount"])
                    if usage_meta.get("promptTokenCount") is not None
                    else None
                ),
                output_tokens=(
                    int(usage_meta["candidatesTokenCount"])
                    if usage_meta.get("candidatesTokenCount") is not None
                    else None
                ),
                reasoning_tokens=(
                    int(usage_meta["thoughtsTokenCount"])
                    if usage_meta.get("thoughtsTokenCount") is not None
                    else None
                ),
            ),
            provider_version=self._provider_version,
        )

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
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
        temperature: float | None = None,
        max_tokens: int,
    ) -> AsyncIterator[ChatCompletionChunk]:
        url = self._url(stream=True)
        body = self._request_body(messages, temperature=temperature, max_tokens=max_tokens)
        client = httpx.AsyncClient(timeout=self._timeout)
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
                    chunk = _gemini_stream_chunk(payload)
                    if chunk is not None:
                        yield chunk
        except httpx.HTTPError as exc:
            msg = "Gemini chat stream failed"
            raise ProviderError(msg, provider_name=self.provider_name) from exc
        finally:
            await client.aclose()


def _gemini_stream_chunk(payload: dict[str, object]) -> ChatCompletionChunk | None:
    """Normalize Gemini's incremental chunk and final usage metadata."""
    candidates = payload.get("candidates")
    candidate = candidates[0] if isinstance(candidates, list) and candidates else None
    delta = ""
    finish_reason: str | None = None
    if isinstance(candidate, dict):
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if isinstance(parts, list):
            delta = "".join(
                str(part["text"])
                for part in parts
                if isinstance(part, dict) and part.get("text") is not None
            )
        raw_finish = candidate.get("finishReason")
        finish_reason = str(raw_finish) if raw_finish is not None else None

    usage_meta = payload.get("usageMetadata")
    usage: ChatUsage | None = None
    if isinstance(usage_meta, dict):
        prompt = usage_meta.get("promptTokenCount")
        completion = usage_meta.get("candidatesTokenCount")
        thoughts = usage_meta.get("thoughtsTokenCount")
        usage = ChatUsage(
            input_tokens=int(prompt) if prompt is not None else None,
            output_tokens=int(completion) if completion is not None else None,
            reasoning_tokens=int(thoughts) if thoughts is not None else None,
        )
    if not delta and finish_reason is None and usage is None:
        return None
    return ChatCompletionChunk(delta=delta, finish_reason=finish_reason, usage=usage)
