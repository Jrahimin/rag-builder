"""Unit tests for one-call turn resolution execution."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

import pytest

from app.modules.conversations.prompts.turn_resolution import TURN_RESOLUTION_PROMPT_VERSION
from app.modules.conversations.turn_resolution import (
    TurnOutcome,
    TurnResolutionInput,
)
from app.modules.conversations.turn_resolver import TurnResolver
from app.platform.providers.contracts.llm import (
    ChatCompletionResult,
    ChatMessage,
    ChatUsage,
)
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.echo_chat import EchoLLMProvider

pytestmark = pytest.mark.unit

_REFERENCE = datetime(2026, 8, 1, tzinfo=UTC)


def _payload(history_content: str = "What is the rebate?") -> TurnResolutionInput:
    return TurnResolutionInput.model_validate(
        {
            "current_message_id": str(uuid.uuid4()),
            "current_message": "And for 90,000?",
            "history": [
                {
                    "id": str(uuid.uuid4()),
                    "role": "user",
                    "content": history_content,
                }
            ],
            "reference_time": _REFERENCE.isoformat(),
        }
    )


class JsonLLM(EchoLLMProvider):
    def __init__(self, payload: dict[str, object] | str) -> None:
        super().__init__(model="test", provider_version="1")
        self.payload = payload
        self.temperature: float | None = 1.0

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None,
        max_tokens: int,
    ) -> ChatCompletionResult:
        self.temperature = temperature
        content = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return ChatCompletionResult(
            content=content,
            provider="echo",
            model="test",
            finish_reason="stop",
            usage=ChatUsage(4, 6),
            provider_version="1",
        )


class SleepingLLM(EchoLLMProvider):
    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None,
        max_tokens: int,
    ) -> ChatCompletionResult:
        del messages, temperature, max_tokens
        await asyncio.sleep(1)
        raise AssertionError("resolver timeout should cancel the provider call")


class CancellingLLM(EchoLLMProvider):
    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None,
        max_tokens: int,
    ) -> ChatCompletionResult:
        del messages, temperature, max_tokens
        raise asyncio.CancelledError


class FailingResolverLLM(EchoLLMProvider):
    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None,
        max_tokens: int,
    ) -> ChatCompletionResult:
        del messages, temperature, max_tokens
        raise ProviderError("boom", provider_name="echo")


async def test_resolver_accepts_strict_json_and_uses_null_temperature() -> None:
    llm = JsonLLM(
        {
            "outcome": "resolved",
            "relation": "follow_up",
            "effective_question": "What rebate applies to 90,000?",
            "active_bindings": [],
            "temporal_intent": {
                "kind": "none",
                "anchor_date": None,
                "requires_snapshot": False,
                "snapshot_origin": None,
            },
            "clarification_question": None,
            "reason": None,
        }
    )
    result = await TurnResolver(llm, timeout_seconds=2, max_output_tokens=256).resolve(
        TurnResolutionInput.model_validate(
            {
                "current_message_id": str(uuid.uuid4()),
                "current_message": "And for 90,000?",
                "history": [
                    {
                        "id": str(uuid.uuid4()),
                        "role": "user",
                        "content": "What is the rebate on 75,000?",
                    }
                ],
                "reference_time": _REFERENCE.isoformat(),
            }
        )
    )
    assert llm.temperature is None
    assert result.attempted is True
    assert result.diagnostics["prompt_version"] == TURN_RESOLUTION_PROMPT_VERSION
    assert result.resolution.outcome is TurnOutcome.RESOLVED
    assert result.retrieval.query == "What rebate applies to 90,000?"
    assert result.diagnostics["query_changed"] is True
    assert result.diagnostics["filter_changed"] is False


async def test_resolver_timeout_and_malformed_output_fall_back() -> None:
    timed_out = await TurnResolver(
        SleepingLLM(model="test", provider_version="1"),
        timeout_seconds=0.05,
        max_output_tokens=32,
    ).resolve(_payload())
    assert timed_out.resolution.outcome is TurnOutcome.FALLBACK
    assert timed_out.diagnostics["failure_code"] == "timeout"
    assert timed_out.retrieval.query == "And for 90,000?"

    malformed = await TurnResolver(
        JsonLLM("not-json"),
        timeout_seconds=2,
        max_output_tokens=32,
    ).resolve(_payload())
    assert malformed.resolution.outcome is TurnOutcome.FALLBACK
    assert malformed.diagnostics["failure_code"] == "malformed_json"
    assert "not-json" not in str(malformed.diagnostics)

    failed = await TurnResolver(
        FailingResolverLLM(model="test", provider_version="1"),
        timeout_seconds=2,
        max_output_tokens=32,
    ).resolve(_payload())
    assert failed.diagnostics["failure_code"] == "provider_failure"


async def test_resolver_cancellation_propagates() -> None:
    with pytest.raises(asyncio.CancelledError):
        await TurnResolver(
            CancellingLLM(model="test", provider_version="1"),
            timeout_seconds=2,
            max_output_tokens=32,
        ).resolve(_payload())


@pytest.mark.parametrize("outcome", ["standalone", "fallback"])
async def test_non_resolution_discards_model_snapshot(outcome: str) -> None:
    payload = _payload()
    result = await TurnResolver(
        JsonLLM(
            {
                "outcome": outcome,
                "relation": "standalone",
                "effective_question": "Changed question",
                "temporal_intent": {"kind": "yesterday", "requires_snapshot": True},
            }
        ),
        timeout_seconds=2,
        max_output_tokens=256,
    ).resolve(payload)
    assert result.retrieval.query == payload.current_message
    assert result.interpretation is None
    assert result.resolution.effective_question == payload.current_message
    assert result.retrieval.as_of is None
    assert result.diagnostics["filter_changed"] is False


@pytest.mark.parametrize(
    ("current_message", "history", "kind", "active_value", "excerpt", "effective_question"),
    [
        (
            "Use that amount.",
            "What is the rebate on 75,000?",
            "scenario_parameter",
            "75,000",
            "75,000",
            "What rebate applies to 90,000?",
        ),
        (
            "Use that date.",
            "What was the rate on 2025-06-01?",
            "period_date",
            "2025-06-01",
            "2025-06-01",
            "What was the rate on 2025-07-01?",
        ),
        (
            "Use that signed amount.",
            "The adjustment is -7,500.",
            "scenario_parameter",
            "-7,500",
            "-7,500",
            "What applies to 7,500?",
        ),
        (
            "Use that duration.",
            "The window is 10 hours.",
            "scenario_parameter",
            "10 hours",
            "10 hours",
            "What applies to 10 days?",
        ),
        (
            "Use that amount.",
            "The budget is BDT 75,000.",
            "scenario_parameter",
            "BDT 75,000",
            "BDT 75,000",
            "What rebate applies to USD 75,000?",
        ),
    ],
)
async def test_resolver_falls_back_when_effective_question_mutates_parameters(
    current_message: str,
    history: str,
    kind: str,
    active_value: str,
    excerpt: str,
    effective_question: str,
) -> None:
    history_id = uuid.uuid4()
    current_id = uuid.uuid4()
    payload = TurnResolutionInput.model_validate(
        {
            "current_message_id": str(current_id),
            "current_message": current_message,
            "history": [{"id": str(history_id), "role": "user", "content": history}],
            "reference_time": _REFERENCE.isoformat(),
        }
    )
    temporal_intent: dict[str, object] = {
        "kind": "none",
        "anchor_date": None,
        "requires_snapshot": False,
        "snapshot_origin": None,
    }
    if kind == "period_date":
        temporal_intent = {
            "kind": "exact_date",
            "anchor_date": active_value,
            "requires_snapshot": True,
            "snapshot_origin": "user_literal",
        }
    result = await TurnResolver(
        JsonLLM(
            {
                "outcome": "resolved",
                "relation": "follow_up",
                "effective_question": effective_question,
                "active_bindings": [
                    {
                        "kind": kind,
                        "active_value": active_value,
                        "origin": "user_literal",
                        "references": [
                            {
                                "message_id": str(history_id),
                                "role": "user",
                                "excerpt": excerpt,
                            }
                        ],
                    }
                ],
                "temporal_intent": temporal_intent,
                "clarification_question": None,
                "reason": None,
            }
        ),
        timeout_seconds=2,
        max_output_tokens=256,
    ).resolve(payload)
    assert result.resolution.outcome is TurnOutcome.FALLBACK
    assert result.retrieval.query == current_message
    assert result.diagnostics["failure_code"] == "mutated_effective_question"
    assert result.diagnostics["failure_field"] == "effective_question"
    assert "rejected_output" not in result.diagnostics
    assert result.diagnostics["query_changed"] is False
    assert result.retrieval.as_of is None


async def test_actual_resolver_snapshot_diagnostics_are_scored_by_journey() -> None:
    from types import SimpleNamespace

    from app.cli.rag_journey import ExpectedResolution, JourneyCase, _resolution_failures

    payload = _payload().model_copy(update={"current_message": "Check on 2025-06-01."})
    result = await TurnResolver(
        JsonLLM(
            {
                "outcome": "resolved",
                "relation": "follow_up",
                "effective_question": "Rate on 2025-06-01?",
                "active_bindings": [
                    {
                        "kind": "period_date",
                        "active_value": "2025-06-01",
                        "origin": "user_literal",
                        "references": [
                            {
                                "message_id": str(payload.current_message_id),
                                "role": "user",
                                "excerpt": "2025-06-01",
                            }
                        ],
                    }
                ],
                "temporal_intent": {
                    "kind": "exact_date",
                    "anchor_date": "2025-06-01",
                    "requires_snapshot": True,
                },
            }
        ),
        timeout_seconds=2,
        max_output_tokens=256,
    ).resolve(payload)
    assert result.retrieval.as_of == datetime(2025, 6, 1, tzinfo=UTC)
    assert result.retrieval.suppress_web
    assert result.diagnostics["filter_changed"] is True
    assert not _resolution_failures(
        case=JourneyCase(
            key="snapshot",
            tags=[],
            query=payload.current_message,
            anchors=[],
            expected_resolution=ExpectedResolution(
                outcome="resolved",
                effective_as_of=result.retrieval.as_of,
                snapshot_origin="user_literal",
            ),
        ),
        message=SimpleNamespace(finish_reason="stop"),
        metadata={"turn_resolution": result.diagnostics},
        prior_turn_messages={},
    )
