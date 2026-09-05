"""One-call turn resolver over the conversation LLM provider."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from app.modules.conversations.prompts.turn_resolution import (
    TURN_RESOLUTION_PROMPT_VERSION,
    build_turn_resolution_messages,
)
from app.modules.conversations.turn_resolution import (
    RESOLUTION_MAX_OUTPUT_TOKENS,
    RESOLUTION_TIMEOUT_SECONDS,
    TURN_RESOLUTION_VERSION,
    EffectiveRetrievalInputs,
    EffectiveSnapshot,
    TemporalIntent,
    TurnOutcome,
    TurnRelation,
    TurnResolution,
    TurnResolutionError,
    TurnResolutionInput,
    effective_retrieval_inputs,
    fallback_resolution,
    parse_resolver_json,
    referenced_message_ids,
    resolve_effective_as_of,
    validate_turn_resolution,
)
from app.platform.providers.contracts.llm import BaseLLMProvider, ChatUsage
from app.platform.providers.errors import ProviderError, ProviderTimeoutError


@dataclass(frozen=True, slots=True)
class ResolvedTurn:
    """Validated interpretation plus compact diagnostics for one chat turn."""

    resolution: TurnResolution
    snapshot: EffectiveSnapshot
    retrieval: EffectiveRetrievalInputs
    diagnostics: dict[str, Any]
    usage: ChatUsage | None
    latency_ms: int
    attempted: bool
    interpretation: str | None


class TurnResolver:
    """Run at most one resolution call, then deterministic validation."""

    def __init__(
        self,
        llm: BaseLLMProvider,
        *,
        timeout_seconds: float,
        max_output_tokens: int,
    ) -> None:
        self._llm = llm
        self._timeout_seconds = max(0.0, min(timeout_seconds, RESOLUTION_TIMEOUT_SECONDS))
        self._max_output_tokens = max(1, min(max_output_tokens, RESOLUTION_MAX_OUTPUT_TOKENS))

    async def resolve(self, payload: TurnResolutionInput) -> ResolvedTurn:
        started = time.perf_counter()
        usage: ChatUsage | None = None
        provider = self._llm.provider_name
        model = self._llm.model_name
        try:
            async with asyncio.timeout(self._timeout_seconds):
                completion = await self._llm.generate(
                    build_turn_resolution_messages(payload),
                    temperature=None,
                    max_tokens=self._max_output_tokens,
                )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return self._fallback(
                payload,
                latency_ms=_elapsed_ms(started),
                failure_code="timeout",
                finish_reason="timeout",
                provider=provider,
                model=model,
            )
        except ProviderTimeoutError as exc:
            return self._fallback(
                payload,
                latency_ms=_elapsed_ms(started),
                failure_code="timeout",
                finish_reason="timeout",
                provider=exc.provider_name or provider,
                model=model,
            )
        except ProviderError as exc:
            return self._fallback(
                payload,
                latency_ms=_elapsed_ms(started),
                failure_code="provider_failure",
                finish_reason="error",
                provider=exc.provider_name or provider,
                model=model,
            )

        latency_ms = _elapsed_ms(started)
        usage = completion.usage
        provider = completion.provider or provider
        model = completion.model or model
        try:
            parsed = parse_resolver_json(completion.content)
        except TurnResolutionError:
            return self._fallback(
                payload,
                latency_ms=latency_ms,
                failure_code="malformed_output",
                finish_reason=completion.finish_reason,
                provider=provider,
                model=model,
                usage=usage,
            )
        try:
            validated = validate_turn_resolution(parsed, payload)
            if validated.outcome is TurnOutcome.FALLBACK:
                return self._fallback(
                    payload,
                    latency_ms=latency_ms,
                    failure_code="unresolved",
                    finish_reason=completion.finish_reason,
                    provider=provider,
                    model=model,
                    usage=usage,
                )
            if validated.outcome is TurnOutcome.STANDALONE:
                validated = TurnResolution(
                    outcome=TurnOutcome.STANDALONE,
                    relation=TurnRelation.STANDALONE,
                    effective_question=payload.current_message,
                    reason=validated.reason,
                )
            snapshot = resolve_effective_as_of(
                request_as_of=payload.request_filters.as_of,
                temporal_intent=(
                    validated.temporal_intent
                    if validated.outcome is TurnOutcome.RESOLVED
                    else TemporalIntent()
                ),
                bindings=validated.active_bindings,
                reference_time=payload.reference_time,
            )
        except TurnResolutionError:
            return self._fallback(
                payload,
                latency_ms=latency_ms,
                failure_code="invalid_references",
                finish_reason=completion.finish_reason,
                provider=provider,
                model=model,
                usage=usage,
            )

        resolution = _apply_snapshot_clarify(validated, snapshot)
        retrieval = effective_retrieval_inputs(
            original_message=payload.current_message,
            resolution=resolution,
            request_filters=payload.request_filters,
            snapshot=snapshot,
        )
        return ResolvedTurn(
            resolution=resolution,
            snapshot=snapshot,
            retrieval=retrieval,
            diagnostics=_diagnostics(
                payload=payload,
                resolution=resolution,
                snapshot=snapshot,
                retrieval=retrieval,
                attempted=True,
                latency_ms=latency_ms,
                provider=provider,
                model=model,
                finish_reason=completion.finish_reason,
                usage=usage,
            ),
            usage=usage,
            latency_ms=latency_ms,
            attempted=True,
            interpretation=(
                _interpretation_text(resolution)
                if resolution.outcome is TurnOutcome.RESOLVED
                else None
            ),
        )

    def _fallback(
        self,
        payload: TurnResolutionInput,
        *,
        latency_ms: int,
        failure_code: str,
        finish_reason: str | None,
        provider: str,
        model: str,
        usage: ChatUsage | None = None,
    ) -> ResolvedTurn:
        resolution = fallback_resolution(payload.current_message)
        snapshot = resolve_effective_as_of(
            request_as_of=payload.request_filters.as_of,
            temporal_intent=resolution.temporal_intent,
            bindings=(),
            reference_time=payload.reference_time,
        )
        retrieval = effective_retrieval_inputs(
            original_message=payload.current_message,
            resolution=resolution,
            request_filters=payload.request_filters,
            snapshot=snapshot,
        )
        return ResolvedTurn(
            resolution=resolution,
            snapshot=snapshot,
            retrieval=retrieval,
            diagnostics=_diagnostics(
                payload=payload,
                resolution=resolution,
                snapshot=snapshot,
                retrieval=retrieval,
                attempted=True,
                latency_ms=latency_ms,
                provider=provider,
                model=model,
                finish_reason=finish_reason,
                usage=usage,
                failure_code=failure_code,
            ),
            usage=usage,
            latency_ms=latency_ms,
            attempted=True,
            interpretation=None,
        )


def bypass_resolution(
    payload: TurnResolutionInput,
    *,
    reason: str,
) -> ResolvedTurn:
    """Skip the model and keep the raw current message."""
    resolution = TurnResolution(
        outcome=TurnOutcome.STANDALONE,
        relation=TurnRelation.STANDALONE,
        effective_question=payload.current_message,
        reason=reason,
    )
    snapshot = resolve_effective_as_of(
        request_as_of=payload.request_filters.as_of,
        temporal_intent=resolution.temporal_intent,
        bindings=resolution.active_bindings,
        reference_time=payload.reference_time,
    )
    retrieval = effective_retrieval_inputs(
        original_message=payload.current_message,
        resolution=resolution,
        request_filters=payload.request_filters,
        snapshot=snapshot,
    )
    return ResolvedTurn(
        resolution=resolution,
        snapshot=snapshot,
        retrieval=retrieval,
        diagnostics=_diagnostics(
            payload=payload,
            resolution=resolution,
            snapshot=snapshot,
            retrieval=retrieval,
            attempted=False,
            latency_ms=0,
            bypass_reason=reason,
        ),
        usage=None,
        latency_ms=0,
        attempted=False,
        interpretation=None,
    )


def _apply_snapshot_clarify(
    resolution: TurnResolution,
    snapshot: EffectiveSnapshot,
) -> TurnResolution:
    if not snapshot.clarify or resolution.outcome is TurnOutcome.CLARIFY:
        return resolution
    return resolution.model_copy(
        update={
            "outcome": TurnOutcome.CLARIFY,
            "clarification_question": (
                resolution.clarification_question
                or (
                    "Your requested date differs from the date selected for this request. "
                    "Which date would you like to use?"
                    if snapshot.clarify_reason and "conflicts" in snapshot.clarify_reason
                    else "Which exact date would you like me to check? Please use YYYY-MM-DD."
                )
            ),
            "reason": resolution.reason or snapshot.clarify_reason,
        }
    )


def _interpretation_text(resolution: TurnResolution) -> str | None:
    if resolution.outcome in {TurnOutcome.FALLBACK, TurnOutcome.CLARIFY}:
        return None
    lines = [
        f"outcome: {resolution.outcome.value}",
        f"relation: {resolution.relation.value}",
        f"current question: {resolution.effective_question}",
    ]
    if resolution.active_bindings:
        bindings = "; ".join(
            f"{binding.kind.value}={binding.active_value} ({binding.origin.value})"
            for binding in resolution.active_bindings
        )
        lines.append(f"conversation bindings: {bindings}")
        lines.append(
            "Only user literals and user-adopted values supply scenario assumptions. "
            "Assistant references identify what to verify; citation references identify "
            "sources. Neither establishes factual authority."
        )
    return "\n".join(lines)


def _diagnostics(
    *,
    payload: TurnResolutionInput,
    resolution: TurnResolution,
    snapshot: EffectiveSnapshot,
    retrieval: EffectiveRetrievalInputs,
    attempted: bool,
    latency_ms: int,
    provider: str | None = None,
    model: str | None = None,
    finish_reason: str | None = None,
    usage: ChatUsage | None = None,
    failure_code: str | None = None,
    bypass_reason: str | None = None,
) -> dict[str, Any]:
    query_changed = retrieval.query != payload.current_message
    diagnostics: dict[str, Any] = {
        "version": TURN_RESOLUTION_VERSION,
        "prompt_version": TURN_RESOLUTION_PROMPT_VERSION,
        "outcome": resolution.outcome.value,
        "relation": resolution.relation.value,
        "reason": resolution.reason,
        "effective_question": resolution.effective_question,
        "active_bindings": [
            binding.model_dump(mode="json") for binding in resolution.active_bindings
        ],
        "referenced_message_ids": [
            str(item) for item in referenced_message_ids(resolution.active_bindings)
        ],
        "history_message_count": len(payload.history),
        "temporal_intent": resolution.temporal_intent.model_dump(mode="json"),
        "snapshot": snapshot.model_dump(mode="json"),
        "query_changed": query_changed,
        "filter_changed": retrieval.as_of != payload.request_filters.as_of,
        "attempted": attempted,
        "latency_ms": latency_ms,
    }
    if bypass_reason is not None:
        diagnostics["bypass_reason"] = bypass_reason
    if provider is not None:
        diagnostics["provider"] = provider
    if model is not None:
        diagnostics["model"] = model
    if finish_reason is not None:
        diagnostics["finish_reason"] = finish_reason
    if usage is not None:
        diagnostics["usage"] = {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
        }
    if failure_code is not None:
        diagnostics["failure_code"] = failure_code
    return diagnostics


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
