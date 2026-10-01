"""LLM provider contract and neutral DTOs.

Integration (provider-agnostic)
-------------------------------
Consumers depend on :class:`BaseLLMProvider` only — never on OpenAI/Gemini/Ollama
implementations.

::

    from app.platform.providers.implementations.llm_factory import get_llm_provider
    from app.platform.providers.contracts.llm import ChatMessage, ChatRole

    llm = get_llm_provider()
    result = await llm.generate(
        [ChatMessage(role=ChatRole.USER, content="Hello")],
        temperature=None,
        max_tokens=1024,
    )

Switch ``APE_LLM__BACKEND`` to change vendor without touching call sites.
See ``docs/learning/conversation_provider_integration.md``.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any


class ChatRole(StrEnum):
    """Chat message roles."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """Neutral chat message for provider input."""

    role: ChatRole
    content: str


@dataclass(frozen=True, slots=True)
class ChatUsage:
    """Token usage from an LLM completion."""

    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ChatCompletionResult:
    """Normalized output from a non-streaming LLM call."""

    content: str
    provider: str
    model: str
    finish_reason: str | None
    usage: ChatUsage
    provider_version: str


@dataclass(frozen=True, slots=True)
class ChatCompletionChunk:
    """One streaming delta from an LLM."""

    delta: str
    finish_reason: str | None = None
    usage: ChatUsage | None = None


@dataclass(frozen=True, slots=True)
class StructuredOutput:
    """Neutral constrained-JSON intent. Semantic/proof validation is mandatory."""

    name: str
    schema: dict[str, Any]


class BaseLLMProvider(ABC):
    """Generate chat completions behind a vendor-neutral interface."""

    supports_output_contract: bool = False

    async def generate_structured(
        self,
        messages: list[ChatMessage],
        *,
        output_contract: StructuredOutput,
        temperature: float | None = None,
        max_tokens: int,
    ) -> ChatCompletionResult:
        """Use adapter constraints or a JSON-only prompt on legacy adapters."""
        if getattr(self, "supports_output_contract", False) is True:
            return await self.generate(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                output_contract=output_contract,
            )
        return await self.generate(
            constrained_messages(messages, output_contract),
            temperature=temperature,
            max_tokens=max_tokens,
        )

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable provider identifier."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model identifier."""

    @property
    @abstractmethod
    def provider_version(self) -> str:
        """Provider implementation version for audit."""

    @abstractmethod
    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int,
        output_contract: StructuredOutput | None = None,
    ) -> ChatCompletionResult:
        """Run a non-streaming chat completion; ``None`` uses provider defaults."""

    @abstractmethod
    def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int,
        output_contract: StructuredOutput | None = None,
    ) -> AsyncGenerator[ChatCompletionChunk, None]:
        """Stream deltas; ``None`` uses provider defaults."""


async def generate_structured(
    provider: BaseLLMProvider,
    messages: list[ChatMessage],
    *,
    output_contract: StructuredOutput,
    temperature: float | None = None,
    max_tokens: int,
) -> ChatCompletionResult:
    """Apply the neutral contract through wrappers and legacy duck-typed ports."""
    return await BaseLLMProvider.generate_structured(
        provider,
        messages,
        output_contract=output_contract,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def constrained_messages(
    messages: list[ChatMessage], output_contract: StructuredOutput
) -> list[ChatMessage]:
    """JSON-only fallback keeps the question and existing retry instruction in place."""
    instruction = "Return only JSON matching " + json.dumps(
        output_contract.schema, ensure_ascii=False
    )
    if messages and messages[0].role is ChatRole.SYSTEM:
        return [
            replace(messages[0], content=messages[0].content + "\n" + instruction),
            *messages[1:],
        ]
    return [ChatMessage(role=ChatRole.SYSTEM, content=instruction), *messages]
