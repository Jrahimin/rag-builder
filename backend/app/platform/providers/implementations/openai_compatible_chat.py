"""OpenAI-compatible chat completions client (OpenAI, vLLM, LiteLLM, etc.)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.logging import get_logger
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

log = get_logger(__name__)


def _role_value(role: ChatRole) -> str:
    return role.value


def _safe_error_value(value: object) -> str | None:
    """Return bounded scalar error data without retaining a response body."""
    if not isinstance(value, (str, int, float, bool)):
        return None
    return str(value)[:500]


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
        temperature: float | None = None,
        max_tokens: int,
        stream: bool,
    ) -> dict[str, object]:
        body: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": _role_value(m.role), "content": m.content} for m in messages],
            "stream": stream,
        }
        capability = describe_llm_capability(self.provider_name, self.model_name)
        if stream and capability.supports_stream_usage:
            body["stream_options"] = {"include_usage": True}
        body.update(
            translate_generation_parameters(
                capability,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        )
        return body

    async def _http_error(
        self,
        response: httpx.Response,
        *,
        operation: str,
    ) -> ProviderError:
        """Build a diagnostic error from explicitly safe upstream error fields.

        Do not log response headers or the request/response body: either may contain
        credentials or user-provided content.  OpenAI-compatible APIs conventionally
        expose these small fields under ``error`` and they are sufficient to diagnose
        unsupported models or parameters.
        """
        body = await response.aread()
        context: dict[str, Any] = {"http_status": response.status_code}
        try:
            payload = json.loads(body)
        except (TypeError, ValueError):
            payload = None

        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            for source, target in (
                ("message", "error_message"),
                ("code", "error_code"),
                ("type", "error_type"),
                ("param", "error_param"),
            ):
                value = _safe_error_value(error.get(source))
                if value is not None:
                    context[target] = value

        log.warning(
            "openai_compatible_chat_request_failed",
            provider=self.provider_name,
            operation=operation,
            **context,
        )
        return ProviderError(
            f"{self.provider_name} {operation} failed (HTTP {response.status_code})",
            provider_name=self.provider_name,
            context=context,
        )

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
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
                if response.is_error:
                    raise await self._http_error(response, operation="chat request")
        except httpx.HTTPError as exc:
            msg = f"{self.provider_name} chat request failed"
            raise ProviderError(
                msg,
                provider_name=self.provider_name,
                context={"http_error_type": type(exc).__name__},
            ) from exc

        payload = response.json()
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            msg = f"{self.provider_name} returned an invalid chat payload"
            raise ProviderError(msg, provider_name=self.provider_name)

        choice = choices[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        finish_reason = choice.get("finish_reason")
        usage = _openai_usage(payload) or ChatUsage(None, None)
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
        temperature: float | None = None,
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
                if response.is_error:
                    raise await self._http_error(response, operation="chat stream")
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
                        usage = _openai_usage(payload)
                        if usage is not None:
                            yield ChatCompletionChunk(delta="", usage=usage)
                        continue
                    delta_obj = choices[0].get("delta") or {}
                    delta = delta_obj.get("content") or ""
                    finish_reason = choices[0].get("finish_reason")
                    if delta or finish_reason:
                        yield ChatCompletionChunk(
                            delta=str(delta),
                            finish_reason=str(finish_reason) if finish_reason else None,
                            usage=_openai_usage(payload),
                        )
        except httpx.HTTPError as exc:
            msg = f"{self.provider_name} chat stream failed"
            raise ProviderError(
                msg,
                provider_name=self.provider_name,
                context={"http_error_type": type(exc).__name__},
            ) from exc
        finally:
            await client.aclose()


def _openai_usage(payload: object) -> ChatUsage | None:
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    return ChatUsage(
        input_tokens=int(prompt) if prompt is not None else None,
        output_tokens=int(completion) if completion is not None else None,
    )
