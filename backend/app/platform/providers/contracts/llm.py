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
        temperature=0.7,
        max_tokens=1024,
    )

Switch ``APE_LLM__BACKEND`` to change vendor without touching call sites.
See ``docs/learning/conversation_provider_integration.md``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum


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

    input_tokens: int
    output_tokens: int


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


class BaseLLMProvider(ABC):
    """Generate chat completions behind a vendor-neutral interface."""

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
        temperature: float,
        max_tokens: int,
    ) -> ChatCompletionResult:
        """Run a non-streaming chat completion."""

    @abstractmethod
    def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Stream chat completion deltas. Must propagate ``asyncio.CancelledError``."""
