"""Deterministic echo LLM provider for tests and local dev."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator

from app.platform.providers.capabilities import (
    describe_llm_capability,
    validate_generation_parameters,
)
from app.platform.providers.contracts.llm import (
    BaseLLMProvider,
    ChatCompletionChunk,
    ChatCompletionResult,
    ChatMessage,
    ChatRole,
    ChatUsage,
    StructuredOutput,
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
        temperature: float | None = None,
        max_tokens: int,
        output_contract: StructuredOutput | None = None,
    ) -> ChatCompletionResult:
        validate_generation_parameters(
            describe_llm_capability(self.provider_name, self.model_name),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        user_text = self._last_user_content(messages)
        content = f"[echo] {user_text}"
        system = messages[0].content if messages else ""
        if "source entailment verifier" in system:
            assertions = json.loads(user_text)
            verdicts = []
            for item in assertions:
                assertion = re.sub(r"\s+", " ", str(item["assertion"])).strip()
                exact = any(
                    assertion == re.sub(r"\s+", " ", str(q["quote"])).strip()
                    for q in item.get("proof", [])
                )
                verdicts.append(
                    {
                        "status": "supported" if exact else "unverified",
                        "failed_dimensions": [] if exact else ["scope"],
                        "evidence_binding": "",
                    }
                )
            content = json.dumps({"verdicts": verdicts})
        elif "Untrusted evidence blocks:" in system:
            evidence = (
                system.split("Untrusted evidence blocks:", 1)[1].split("\n\nEnd of", 1)[0].strip()
            )
            passage = re.sub(r"^\[\d+\][^\n]*\n", "", evidence).strip()
            content = "[echo] " + passage + " [1]"
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
        temperature: float | None = None,
        max_tokens: int,
        output_contract: StructuredOutput | None = None,
    ) -> AsyncGenerator[ChatCompletionChunk, None]:
        validate_generation_parameters(
            describe_llm_capability(self.provider_name, self.model_name),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        result = await self.generate(messages, temperature=None, max_tokens=1)
        words = result.content.split(" ")
        for index, word in enumerate(words):
            delta = word if index == 0 else f" {word}"
            yield ChatCompletionChunk(delta=delta)
        yield ChatCompletionChunk(delta="", finish_reason="stop", usage=result.usage)
