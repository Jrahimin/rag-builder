"""Ollama chat provider."""

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
        self._context_window: int | None = None

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

    async def _require_supported_output_limit(
        self,
        client: httpx.AsyncClient,
        max_tokens: int,
    ) -> None:
        if self._context_window is None:
            response = await client.post(
                f"{self._base_url}/api/show",
                json={"model": self._model},
            )
            response.raise_for_status()
            self._context_window = _context_window_from_show(response.json())
        if self._context_window is None:
            raise ProviderError(
                "Ollama did not report a context length for the configured model",
                provider_name=self.provider_name,
            )
        if max_tokens > self._context_window:
            raise ProviderError(
                (
                    f"Requested max_tokens ({max_tokens}) exceeds the Ollama model "
                    f"context window ({self._context_window})."
                ),
                provider_name=self.provider_name,
                context={"max_tokens": max_tokens, "context_window": self._context_window},
            )

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int,
    ) -> ChatCompletionResult:
        url = f"{self._base_url}/api/chat"
        body: dict[str, object] = {
            "model": self._model,
            "messages": self._ollama_messages(messages),
            "stream": False,
        }
        body["options"] = translate_generation_parameters(
            describe_llm_capability(self.provider_name, self.model_name),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                await self._require_supported_output_limit(client, max_tokens)
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
                input_tokens=(
                    int(payload["prompt_eval_count"])
                    if payload.get("prompt_eval_count") is not None
                    else None
                ),
                output_tokens=(
                    int(payload["eval_count"])
                    if payload.get("eval_count") is not None
                    else None
                ),
            ),
            provider_version=self._provider_version,
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int,
    ) -> AsyncIterator[ChatCompletionChunk]:
        url = f"{self._base_url}/api/chat"
        body: dict[str, object] = {
            "model": self._model,
            "messages": self._ollama_messages(messages),
            "stream": True,
        }
        body["options"] = translate_generation_parameters(
            describe_llm_capability(self.provider_name, self.model_name),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        client = httpx.AsyncClient(timeout=self._timeout)
        try:
            await self._require_supported_output_limit(client, max_tokens)
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
                        yield ChatCompletionChunk(
                            delta="",
                            finish_reason="stop",
                            usage=ChatUsage(
                                input_tokens=(
                                    int(payload["prompt_eval_count"])
                                    if payload.get("prompt_eval_count") is not None
                                    else None
                                ),
                                output_tokens=(
                                    int(payload["eval_count"])
                                    if payload.get("eval_count") is not None
                                    else None
                                ),
                            ),
                        )
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


def _context_window_from_show(payload: object) -> int | None:
    """Read the model's actual context and any lower Modelfile num_ctx."""
    if not isinstance(payload, dict):
        return None
    model_info = payload.get("model_info")
    reported = (
        [
            int(value)
            for key, value in model_info.items()
            if str(key).endswith(".context_length")
            and isinstance(value, int)
            and value > 0
        ]
        if isinstance(model_info, dict)
        else []
    )
    model_context = min(reported) if reported else None
    parameters = payload.get("parameters")
    configured_context: int | None = None
    if isinstance(parameters, str):
        for line in parameters.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == "num_ctx":
                try:
                    value = int(parts[1])
                except ValueError:
                    continue
                if value > 0:
                    configured_context = value
    if model_context is None:
        return configured_context
    if configured_context is None:
        return model_context
    return min(model_context, configured_context)
