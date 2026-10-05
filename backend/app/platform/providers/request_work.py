"""Turn-local provider work reuse and aggregate telemetry (never an answer cache)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections import Counter
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager, nullcontext
from contextvars import ContextVar, Token
from functools import wraps
from typing import Any, TypeVar, cast

from app.platform.providers.contracts.embedding import (
    BaseEmbeddingProvider,
    EmbeddingBatchResult,
    EmbeddingPurpose,
)
from app.platform.providers.contracts.llm import (
    BaseLLMProvider,
    ChatCompletionChunk,
    ChatCompletionResult,
    ChatMessage,
    StructuredOutput,
    constrained_messages,
    generate_structured,
)
from app.platform.providers.errors import (
    ProviderError,
    ProviderTimeoutError,
    sanitized_provider_failure_reason,
)
from app.platform.providers.prompt_budget import prompt_budget

_AsyncMethod = TypeVar("_AsyncMethod", bound=Callable[..., Awaitable[Any]])
_DEFAULT_MAX_SPAN_DETAIL = 80
_MAX_SPAN_CALL_INDEXES = 32
_current_work: ContextVar[RequestWork | None] = ContextVar("ape_request_work", default=None)
_current_span_id: ContextVar[str | None] = ContextVar("ape_request_span_id", default=None)
_current_purpose: ContextVar[str | None] = ContextVar("ape_request_purpose", default=None)


def _reset_contextvar(var: ContextVar[Any], token: Token[Any]) -> None:
    """Reset when this task still owns the token.

    Async generators may be finalized in another task after a disconnect, which
    raises ``ValueError: was created in a different Context``.
    """

    try:
        var.reset(token)
    except ValueError:
        return


def current_request_work() -> RequestWork | None:
    """Return the turn-local work bound to this task, if any."""

    return _current_work.get()


def current_request_purpose() -> str | None:
    """Return the task-local provider-call purpose, if any."""

    return _current_purpose.get()


def current_request_span_id() -> str | None:
    """Return the task-local parent span id, if any."""

    return _current_span_id.get()


def sanitized_error_category(exc: BaseException) -> str:
    """Classify a failure without prompts, source text, or raw provider messages."""

    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    if isinstance(exc, ProviderError):
        reason = sanitized_provider_failure_reason(exc)
        if reason != "unavailable":
            return reason
        code = getattr(exc, "code", None)
        if isinstance(code, str) and code:
            return code[:80]
        return "provider_error"
    return type(exc).__name__[:80]


def observe_stage(name: str) -> Callable[[_AsyncMethod], _AsyncMethod]:
    """Instrument service boundaries without retaining inputs or candidate text."""

    def decorate(method: _AsyncMethod) -> _AsyncMethod:
        @wraps(method)
        async def observed(self: Any, *args: Any, **kwargs: Any) -> Any:
            work = getattr(self, "_work", None) or getattr(
                getattr(self, "_embedder", None), "work", None
            )
            if not isinstance(work, RequestWork):
                work = current_request_work()
            if not isinstance(work, RequestWork):
                return await method(self, *args, **kwargs)
            with work.stage(name):
                return await method(self, *args, **kwargs)

        return cast(_AsyncMethod, observed)

    return decorate


class RequestWork:
    """One instance per turn, shared only by that turn's retrieval branches."""

    def __init__(
        self, project_id: uuid.UUID, *, max_span_detail: int = _DEFAULT_MAX_SPAN_DETAIL
    ) -> None:
        self.project_id = project_id
        self.started = time.perf_counter()
        self.timings: Counter[str] = Counter()
        self.counts: Counter[str] = Counter()
        self.vectors: dict[tuple[object, ...], list[float]] = {}
        self.content: dict[tuple[object, ...], object] = {}
        self.evidence_snapshot: dict[str, Any] = {}
        self.embedding_lock = asyncio.Lock()
        self.embedding_futures: dict[tuple[object, ...], asyncio.Future[list[float]]] = {}
        self.deadline = self.started + 60.0
        self.calls: list[dict[str, Any]] = []
        self.validation_retries: list[dict[str, Any]] = []
        self.max_span_detail = max(1, max_span_detail)
        self._spans: list[dict[str, Any]] = []
        self._omitted_spans = 0
        self._active: Counter[str] = Counter()
        self._open: dict[str, dict[str, Any]] = {}
        self._span_started: dict[str, float] = {}
        self._unbound_purpose: str | None = None
        self.execution_policy = "legacy"
        self.budget_class = "simple"
        self.promoted = False
        self.promotion_reason: str | None = None
        self._outer_timeout: asyncio.Timeout | None = None
        self._reschedule_deadline: Callable[[float], None] | None = None
        self.stage_estimates: dict[str, dict[str, Any]] = {}
        self.completed_stages: set[str] = set()

    def configure_execution(
        self,
        policy: str,
        *,
        complex_question: bool = False,
        estimates: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Freeze policy and measured estimates once per request, never per retry."""
        if self.counts["execution_policy_frozen"]:
            return
        self.counts["execution_policy_frozen"] += 1
        self.execution_policy = policy
        if policy != "adaptive_v1":
            return
        self.budget_class = "complex" if complex_question else "simple"
        self.deadline = self.started + (120.0 if complex_question else 45.0) - 2.0
        for name, floor in {
            "coverage": 10.0,
            "generation": 8.0,
            "verification": 12.0,
            "persistence": 2.0,
        }.items():
            sample = (estimates or {}).get(name, {})
            measured = (
                float(sample.get("p95_seconds", 0)) if int(sample.get("samples", 0)) >= 20 else 0.0
            )
            self.stage_estimates[name] = {
                "seconds": max(floor, measured),
                "origin": "measured_p95" if measured > floor else "cold_start_floor",
                "samples": int(sample.get("samples", 0)),
                "estimate_id": sample.get("estimate_id"),
            }

        self.deadline = self.request_deadline - self.reserve("persistence")

    def freeze_measured_stages(self, samples: list[dict[str, Any]]) -> None:
        """Freeze larger p95s from completed spans, never sum overlapping spans."""
        if self.execution_policy != "adaptive_v1" or self.counts["stage_samples_frozen"]:
            return
        self.counts["stage_samples_frozen"] += 1
        aliases = {
            "coverage": "coverage_review",
            "generation": "answer_generation",
            "verification": "claim_verification",
            "persistence": "persistence",
        }
        for stage, span_name in aliases.items():
            measured: list[tuple[float, str]] = []
            for sample in samples:
                lifecycle = (sample.get("metadata") or {}).get("lifecycle") or {}
                if (lifecycle.get("deadline") or {}).get("budget_class") != self.budget_class:
                    continue
                spans = (lifecycle.get("spans") or {}).get("items") or []
                completed = [
                    float(item.get("elapsed_ms", 0)) / 1000
                    for item in spans
                    if item.get("name") == span_name and item.get("outcome") == "completed"
                ]
                if completed:
                    measured.append((max(completed), str(sample.get("message_id"))))
            if len(measured) < 20:
                continue
            ordered = sorted(value for value, _ in measured)
            p95 = ordered[(95 * len(ordered) + 99) // 100 - 1]
            if p95 > float(self.stage_estimates[stage]["seconds"]):
                provenance = hashlib.sha256(
                    "\n".join(sorted(identity for _, identity in measured)).encode()
                ).hexdigest()
                self.stage_estimates[stage] = {
                    "seconds": p95,
                    "origin": "measured_p95",
                    "samples": len(measured),
                    "estimate_id": provenance,
                }
        self.deadline = self.request_deadline - self.reserve("persistence")
        if self._reschedule_deadline is not None:
            self._reschedule_deadline(self.request_deadline)

    @asynccontextmanager
    async def execution_timeout(self) -> AsyncIterator[None]:
        """The actual outer timer is rescheduled when the request is promoted."""
        loop = asyncio.get_running_loop()
        ceiling = self.request_deadline if self.execution_policy == "adaptive_v1" else self.deadline
        timer = asyncio.timeout_at(loop.time() + max(0.0, ceiling - time.perf_counter()))
        self._outer_timeout = timer
        self._reschedule_deadline = lambda deadline: timer.reschedule(
            loop.time() + max(0.0, deadline - time.perf_counter())
        )
        try:
            async with timer:
                yield
        finally:
            self._outer_timeout = None
            self._reschedule_deadline = None

    def promote(
        self,
        requirement_id: str,
        *,
        recoverable: bool,
        known_corpus_gap: bool = False,
        required: bool = True,
        required_seconds: float | None = None,
    ) -> bool:
        if (
            self.execution_policy != "adaptive_v1"
            or self.budget_class == "complex"
            or self.promoted
            or not required
            or not recoverable
            or known_corpus_gap
            or not requirement_id
            or (
                required_seconds is not None
                and time.perf_counter() + required_seconds <= self.recovery_deadline
            )
        ):
            return False
        self.promoted = True
        self.budget_class = "complex"
        self.promotion_reason = requirement_id
        self.deadline = self.started + 120.0 - self.reserve("persistence")
        if self._reschedule_deadline is not None:
            self._reschedule_deadline(self.request_deadline)
        return True

    def reserve(self, stage: str) -> float:
        return (
            0.0
            if stage in self.completed_stages
            else float(self.stage_estimates.get(stage, {}).get("seconds", 0))
        )

    def complete_stage(self, stage: str) -> None:
        self.completed_stages.add(stage)

    def claim_correction(self, kind: str) -> bool:
        key = "malformed_correction_exchanges" if kind == "malformed" else "semantic_repairs"
        if self.counts[key] >= 1:
            return False
        self.counts[key] += 1
        return True

    @property
    def recovery_search_credits(self) -> int:
        return 6 if self.execution_policy == "adaptive_v1" and self.budget_class == "complex" else 3

    def attach(self) -> Token[RequestWork | None]:
        """Bind this turn to the current task so parallel branches keep separate parents."""

        return _current_work.set(self)

    def detach(self, token: Token[RequestWork | None]) -> None:
        _reset_contextvar(_current_work, token)

    @contextmanager
    def attached(self) -> Iterator[RequestWork]:
        token = self.attach()
        try:
            yield self
        finally:
            self.detach(token)

    @property
    def request_deadline(self) -> float:
        return self.started + (
            (120.0 if self.budget_class == "complex" else 45.0)
            if self.execution_policy == "adaptive_v1"
            else 60.0
        )

    @property
    def recovery_deadline(self) -> float:
        if self.execution_policy == "adaptive_v1":
            return self.request_deadline - sum(
                self.reserve(stage)
                for stage in ("coverage", "generation", "verification", "persistence")
            )
        # 10 generation + 10 verification + 10 terminal persistence seconds.
        return min(self.deadline - 20.0, self.request_deadline - 30.0)

    def phase_deadline(self, purpose: str | None = None) -> float:
        purpose = purpose or current_request_purpose() or self._unbound_purpose
        if self.execution_policy == "adaptive_v1":
            if purpose == "persistence":
                return self.request_deadline
            if purpose in {"claim_verification", "semantic_repair"}:
                return self.request_deadline - self.reserve("persistence")
            if purpose in {"answer_generation", "answer_shape_correction"}:
                return (
                    self.request_deadline
                    - self.reserve("verification")
                    - self.reserve("persistence")
                )
            if purpose in {
                "coverage_review",
                "scenario_input_review",
                "web_evidence_review",
                "selector_retry",
                "structured_response_retry",
            }:
                return self.request_deadline - sum(
                    self.reserve(stage) for stage in ("generation", "verification", "persistence")
                )
            return self.recovery_deadline
        if purpose in {"answer_generation", "answer_shape_correction"}:
            return self.deadline - 10.0
        if purpose in {
            "recovery_planning",
            "coverage_review",
            "structured_response_retry",
            "selector_retry",
            "scenario_input_review",
            "web_evidence_review",
            "turn_resolution",
        }:
            return self.recovery_deadline
        if purpose in {"claim_verification", "persistence"}:
            return self.deadline
        return min(self.deadline, self.recovery_deadline)

    def phase_timeout_context(self) -> dict[str, str]:
        return {
            "reason": "recovery_deadline_exceeded"
            if self.phase_deadline() == self.recovery_deadline
            else "request_deadline_exceeded",
            "phase": current_request_purpose() or self._unbound_purpose or "coverage",
        }

    def active_purposes(self) -> set[str]:
        """Return purposes with at least one open span or purpose context on this turn."""

        return {name for name, count in self._active.items() if count > 0}

    @contextmanager
    def purpose(self, name: str) -> Iterator[None]:
        """Propagate purpose to provider calls without opening another span."""

        token = _current_purpose.set(name)
        self._active[name] += 1
        try:
            yield
        finally:
            self._active[name] -= 1
            if self._active[name] <= 0:
                del self._active[name]
            _reset_contextvar(_current_purpose, token)

    @contextmanager
    def stage(self, name: str, *, purpose: str | None = None) -> Iterator[None]:
        started = time.perf_counter()
        purpose_name = purpose or name
        span = self._open_span(name, purpose_name, started)
        span_token = _current_span_id.set(span["id"])
        purpose_token = _current_purpose.set(purpose_name)
        self._active[purpose_name] += 1
        outcome = "completed"
        error: BaseException | None = None
        try:
            yield
        except asyncio.CancelledError as exc:
            outcome = "cancelled"
            error = exc
            raise
        except GeneratorExit:
            outcome = "cancelled"
            raise
        except Exception as exc:
            outcome = "failed"
            error = exc
            raise
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            self.timings[name] += elapsed_ms
            self._active[purpose_name] -= 1
            if self._active[purpose_name] <= 0:
                del self._active[purpose_name]
            self._close_span(span, elapsed_ms, outcome, error)
            _reset_contextvar(_current_span_id, span_token)
            _reset_contextvar(_current_purpose, purpose_token)

    def begin_span(self, name: str, *, purpose: str | None = None) -> dict[str, Any]:
        """Open a span without binding task-local context across generator yields."""
        started = time.perf_counter()
        purpose_name = purpose or name
        span = self._open_span(name, purpose_name, started)
        self._span_started[span["id"]] = started
        self._active[purpose_name] += 1
        self._unbound_purpose = purpose_name
        return span

    def finish_span(
        self,
        span: dict[str, Any],
        *,
        outcome: str = "completed",
        error: BaseException | None = None,
    ) -> None:
        started = self._span_started.pop(span["id"], time.perf_counter())
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        self.timings[span["name"]] += elapsed_ms
        purpose_name = str(span.get("purpose") or span["name"])
        self._active[purpose_name] -= 1
        if self._active[purpose_name] <= 0:
            del self._active[purpose_name]
        if self._unbound_purpose == purpose_name:
            self._unbound_purpose = None
        self._close_span(span, elapsed_ms, outcome, error)

    @asynccontextmanager
    async def wait(self, name: str) -> AsyncIterator[None]:
        """Measure a wait, then exit so the caller can run the protected operation."""

        started = time.perf_counter()
        outcome = "completed"
        error: BaseException | None = None
        try:
            yield
        except asyncio.CancelledError as exc:
            outcome = "cancelled"
            error = exc
            raise
        except Exception as exc:
            outcome = "failed"
            error = exc
            raise
        finally:
            self.record_wait(name, started, outcome=outcome, error=error)

    def record_wait(
        self,
        name: str,
        started: float,
        *,
        outcome: str = "completed",
        error: BaseException | None = None,
    ) -> None:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        self.timings[name] += elapsed_ms
        self.counts[f"{name}_waits"] += 1
        span = self._open_span(name, name, started)
        self._close_span(span, elapsed_ms, outcome, error)

    def annotate_provider_call(
        self, call: dict[str, Any], *, purpose_field: str = "purpose"
    ) -> None:
        """Associate a provider attempt with the task-local purpose and span."""

        purpose = _current_purpose.get() or self._unbound_purpose
        span_id = _current_span_id.get()
        index = next((i for i, item in enumerate(self.calls) if item is call), None)
        if index is None:
            index = len(self.calls)
            self.calls.append(call)
        if purpose and purpose_field not in call:
            call[purpose_field] = purpose
        if span_id:
            call["span_id"] = span_id
            span = self._open.get(span_id)
            if span is not None:
                indexes = span.setdefault("provider_call_indexes", [])
                if len(indexes) < _MAX_SPAN_CALL_INDEXES and index not in indexes:
                    indexes.append(index)

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": "turn.v1",
            "deadline": {
                "request_seconds": round(self.request_deadline - self.started, 3),
                "recovery_seconds": round(max(0.0, self.recovery_deadline - self.started), 3),
                "generation_reserve_seconds": self.reserve("generation")
                if self.execution_policy == "adaptive_v1"
                else 10,
                "verification_reserve_seconds": self.reserve("verification")
                if self.execution_policy == "adaptive_v1"
                else 10,
                "persistence_reserve_seconds": self.reserve("persistence")
                if self.execution_policy == "adaptive_v1"
                else 10,
                "policy": self.execution_policy,
                "budget_class": self.budget_class,
                "p95_target_seconds": 90 if self.budget_class == "complex" else 30,
                "promoted": self.promoted,
                "promotion_requirement_id": self.promotion_reason,
                "stage_estimates": {k: dict(v) for k, v in self.stage_estimates.items()},
                "completed_stages": sorted(self.completed_stages),
            },
            "processing_ms": round((time.perf_counter() - self.started) * 1000),
            "stages_ms": dict(self.timings),
            "counts": dict(self.counts),
            "provider_calls": list(self.calls),
            "validation_retries": list(self.validation_retries),
            "stage_semantics": "elapsed stage durations may overlap across parallel branches",
            "snapshot_includes_persistence": "persistence" in self.timings,
            "calls_by_purpose": dict(self._calls_by_purpose()),
            "tokens_by_purpose": self._tokens_by_purpose(),
            "spans": {
                "version": "turn.spans.v1",
                "items": [dict(span) for span in self._spans],
                "omitted": self._omitted_spans,
                "max_items": self.max_span_detail,
                "overlap": "span elapsed values are not a wall-clock sum",
            },
        }

    def wrap(self, provider: BaseEmbeddingProvider) -> BaseEmbeddingProvider:
        if isinstance(provider, CachedEmbeddingProvider) and provider.work is self:
            return provider
        return CachedEmbeddingProvider(provider, self)

    def _open_span(self, name: str, purpose: str, started: float) -> dict[str, Any]:
        span: dict[str, Any] = {
            "id": uuid.uuid4().hex[:12],
            "parent_id": _current_span_id.get(),
            "name": name,
            "purpose": purpose,
            "start_offset_ms": max(0, round((started - self.started) * 1000)),
            "elapsed_ms": 0,
            "outcome": "completed",
        }
        self.counts["spans"] += 1
        self._open[span["id"]] = span
        if len(self._spans) < self.max_span_detail:
            self._spans.append(span)
        else:
            self._omitted_spans += 1
            self.counts["spans_omitted"] += 1
        return span

    def _close_span(
        self,
        span: dict[str, Any],
        elapsed_ms: int,
        outcome: str,
        error: BaseException | None,
    ) -> None:
        span["elapsed_ms"] = elapsed_ms
        span["outcome"] = outcome
        if error is not None and outcome == "failed":
            span["error_category"] = sanitized_error_category(error)
        self._open.pop(span["id"], None)

    def _call_purpose(self, call: dict[str, Any]) -> str:
        purpose = call.get("work_purpose") or call.get("purpose")
        if isinstance(purpose, str) and purpose:
            return purpose
        return "unspecified"

    def _calls_by_purpose(self) -> Counter[str]:
        counts: Counter[str] = Counter()
        for call in self.calls:
            if call.get("kind") in {"llm", "embedding", "rerank"}:
                counts[self._call_purpose(call)] += 1
        return counts

    def _tokens_by_purpose(self) -> dict[str, dict[str, int | None]]:
        totals: dict[str, dict[str, int | None]] = {}
        for call in self.calls:
            if call.get("kind") not in {"llm", "embedding"}:
                continue
            purpose = self._call_purpose(call)
            bucket = totals.setdefault(purpose, {"input": 0, "output": 0})
            for field, key in (("input_tokens", "input"), ("output_tokens", "output")):
                value = call.get(field)
                if value is None or bucket[key] is None:
                    if value is None:
                        bucket[key] = None
                    continue
                bucket[key] = int(bucket[key] or 0) + int(value)
        return totals


class ObservedLLM(BaseLLMProvider):
    supports_output_contract = True
    """Record actual call attempts and provider-reported usage, without prompt text."""

    def __init__(
        self, provider: BaseLLMProvider, work: RequestWork, *, capacity: int | None = None
    ) -> None:
        self.provider = provider
        self.work = work
        self.capacity = capacity

    def _check_budget(self, messages: list[ChatMessage], max_tokens: int) -> None:
        if self.capacity is None:
            return
        budget = prompt_budget(
            messages, model=self.model_name, capacity=self.capacity, reserved_output=max_tokens
        )
        if not budget["within_budget"]:
            self.work.counts["prompt_budget_rejections"] += 1
            self.work.calls.append({"kind": "prompt_budget", "status": "rejected", **budget})
            raise ProviderError(
                "Prompt and output reserve exceed the configured model context budget",
                provider_name=self.provider_name,
                context={"reason": "prompt_budget_exceeded"},
            )

    @property
    def provider_name(self) -> str:
        return self.provider.provider_name

    @property
    def model_name(self) -> str:
        return self.provider.model_name

    @property
    def provider_version(self) -> str:
        return self.provider.provider_version

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int,
        output_contract: StructuredOutput | None = None,
    ) -> ChatCompletionResult:
        self._check_budget(messages, max_tokens)
        self._require_phase_budget()
        with self._call() as call:
            call.update(self._request_provenance(output_contract, max_tokens))
            try:
                async with asyncio.timeout(
                    max(0.0, self.work.phase_deadline() - time.perf_counter())
                ):
                    if output_contract is not None:
                        result = await generate_structured(
                            self.provider,
                            messages,
                            output_contract=output_contract,
                            temperature=temperature,
                            max_tokens=max_tokens,
                        )
                    else:
                        result = await self.provider.generate(
                            messages, temperature=temperature, max_tokens=max_tokens
                        )
            except asyncio.CancelledError:
                call["status"] = "cancelled"
                raise
            except TimeoutError as exc:
                raise ProviderTimeoutError(
                    "The shared request deadline was exhausted.",
                    provider_name=self.provider_name,
                    context=self.work.phase_timeout_context(),
                ) from exc
            call.update(
                status="completed",
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                reasoning_tokens=result.usage.reasoning_tokens,
            )
            provenance = getattr(result, "provenance", None)
            if isinstance(provenance, dict):
                call.update(provenance)
            return result

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int,
        output_contract: StructuredOutput | None = None,
    ) -> AsyncGenerator[ChatCompletionChunk, None]:
        self._check_budget(messages, max_tokens)
        self._require_phase_budget()
        call, started = self._open_call()
        call.update(self._request_provenance(output_contract, max_tokens))
        if (
            output_contract is not None
            and getattr(self.provider, "supports_output_contract", False) is not True
        ):
            messages = constrained_messages(messages, output_contract)
        if (
            output_contract is not None
            and getattr(self.provider, "supports_output_contract", False) is True
        ):
            upstream = self.provider.stream(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                output_contract=output_contract,
            )
        else:
            upstream = self.provider.stream(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        purpose = call.get("purpose")
        try:
            while True:
                # Bind only while advancing the provider, never across a yield to
                # the transport (which may resume/finalize in another task).
                with self.work.purpose(str(purpose)) if purpose else nullcontext():
                    try:
                        async with asyncio.timeout(
                            max(0.0, self.work.phase_deadline() - time.perf_counter())
                        ):
                            self._require_phase_budget()
                            chunk = await anext(upstream)
                    except StopAsyncIteration:
                        break
                if chunk.usage is not None:
                    call.update(
                        input_tokens=chunk.usage.input_tokens,
                        output_tokens=chunk.usage.output_tokens,
                        reasoning_tokens=chunk.usage.reasoning_tokens,
                    )
                yield chunk
            call["status"] = "completed"
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                "The shared request deadline was exhausted.",
                provider_name=self.provider_name,
                context=self.work.phase_timeout_context(),
            ) from exc
        except (asyncio.CancelledError, GeneratorExit):
            call["status"] = "cancelled"
            raise
        finally:
            call["duration_ms"] = round((time.perf_counter() - started) * 1000)
            await upstream.aclose()

    def _request_provenance(
        self, contract: StructuredOutput | None, max_tokens: int
    ) -> dict[str, Any]:
        describe = getattr(self.provider, "request_provenance", None)
        descriptor = describe(contract, max_tokens) if callable(describe) else None
        base = {
            "schema_mode": "prompt" if contract else "none",
            "reasoning": "provider_default",
            "local_validation": "consumer_schema_required" if contract else "not_applicable",
            "purpose": current_request_purpose() or "unspecified",
            "model": self.model_name,
        }
        if isinstance(descriptor, dict):
            base.update(descriptor)
        base["purpose"] = current_request_purpose() or self.work._unbound_purpose or base["purpose"]
        if contract:
            base["schema_name"] = contract.name
            base["schema_hash"] = hashlib.sha256(
                json.dumps(contract.schema, sort_keys=True).encode()
            ).hexdigest()
        return base

    def _require_phase_budget(self) -> None:
        if self.work.phase_deadline() <= time.perf_counter():
            raise ProviderTimeoutError(
                "The phase deadline was exhausted before provider admission.",
                provider_name=self.provider_name,
                context=self.work.phase_timeout_context(),
            )

    def _open_call(self) -> tuple[dict[str, Any], float]:
        call: dict[str, Any] = {
            "kind": "llm",
            "provider": self.provider_name,
            "model": self.model_name,
            "status": "failed",
            "input_tokens": None,
            "output_tokens": None,
            "provider_internal_retries": None,
        }
        started = time.perf_counter()
        self.work.counts["llm_calls"] += 1
        self.work.annotate_provider_call(call)
        return call, started

    @contextmanager
    def _call(self) -> Iterator[dict[str, Any]]:
        call, started = self._open_call()
        try:
            yield call
        finally:
            call["duration_ms"] = round((time.perf_counter() - started) * 1000)


class CachedEmbeddingProvider(BaseEmbeddingProvider):
    """Deduplicate exact texts; query/document purpose and full identity stay distinct."""

    def __init__(self, provider: BaseEmbeddingProvider, work: RequestWork) -> None:
        self.provider = provider
        self.work = work

    @property
    def provider_name(self) -> str:
        return self.provider.provider_name

    @property
    def model_name(self) -> str:
        return self.provider.model_name

    @property
    def dimensions(self) -> int:
        return self.provider.dimensions

    @property
    def provider_version(self) -> str:
        return self.provider.provider_version

    async def embed_texts(
        self, texts: list[str], *, purpose: EmbeddingPurpose = EmbeddingPurpose.DOCUMENT
    ) -> EmbeddingBatchResult:
        billed: int | None = 0
        prefix = (
            self.work.project_id,
            self.provider_name,
            self.model_name,
            self.provider_version,
            self.dimensions,
            purpose.value,
        )
        keys: list[tuple[object, ...]] = [
            (*prefix, hashlib.sha256(text.encode("utf-8")).hexdigest()) for text in texts
        ]
        # Register per-key ownership atomically; unrelated texts run independently.
        owners: dict[tuple[object, ...], str] = {}
        futures: dict[tuple[object, ...], asyncio.Future[list[float]]] = {}
        async with self.work.embedding_lock:
            for key, text in zip(keys, texts, strict=True):
                if key in self.work.vectors:
                    self.work.counts["embedding_cache_hits"] += 1
                    continue
                future = self.work.embedding_futures.get(key)
                if future is None:
                    future = asyncio.get_running_loop().create_future()
                    # Retrieve orphan exceptions if a waiter was cancelled.
                    future.add_done_callback(
                        lambda value: value.exception() if not value.cancelled() else None
                    )
                    self.work.embedding_futures[key] = future
                    owners[key] = text
                else:
                    self.work.counts["embedding_cache_hits"] += 1
                futures[key] = future
        if owners:
            started = time.perf_counter()
            call: dict[str, Any] = {
                "kind": "embedding",
                "provider": self.provider_name,
                "model": self.model_name,
                "purpose": purpose.value,
                "texts": len(owners),
                "input_tokens": None,
                "output_tokens": None,
                "status": "failed",
                "provider_internal_retries": None,
            }
            self.work.counts["embedding_calls"] += 1
            self.work.counts["embedded_texts"] += len(owners)
            self.work.annotate_provider_call(call, purpose_field="work_purpose")
            try:
                async with asyncio.timeout(
                    max(0.0, self.work.phase_deadline() - time.perf_counter())
                ):
                    if self.work.phase_deadline() <= time.perf_counter():
                        raise ProviderTimeoutError(
                            "Embedding phase deadline exhausted before provider admission.",
                            provider_name=self.provider_name,
                            context=self.work.phase_timeout_context(),
                        )
                    result = await self.provider.embed_texts(list(owners.values()), purpose=purpose)
                if (
                    (result.provider, result.model, result.dimensions, result.provider_version)
                    != (self.provider_name, self.model_name, self.dimensions, self.provider_version)
                    or len(result.vectors) != len(owners)
                    or any(len(vector) != self.dimensions for vector in result.vectors)
                ):
                    raise ProviderError(
                        "Embedding result identity or vector shape mismatch",
                        provider_name=self.provider_name,
                        context={"reason": "embedding_identity_mismatch"},
                    )
                for key, vector in zip(owners, result.vectors, strict=True):
                    self.work.vectors[key] = list(vector)
                    futures[key].set_result(list(vector))
                call["status"] = "completed"
                call["input_tokens"] = result.billed_input_tokens
                billed = result.billed_input_tokens
            except BaseException as exc:
                if isinstance(exc, TimeoutError):
                    exc = ProviderTimeoutError(
                        "The shared request deadline was exhausted.",
                        provider_name=self.provider_name,
                        context=self.work.phase_timeout_context(),
                    )
                for key in owners:
                    if not futures[key].done():
                        futures[key].set_exception(exc)
                raise exc
            finally:
                for key in owners:
                    self.work.embedding_futures.pop(key, None)
                call["duration_ms"] = round((time.perf_counter() - started) * 1000)
                self.work.timings[
                    "query_embedding" if purpose is EmbeddingPurpose.QUERY else "document_embedding"
                ] += call["duration_ms"]
        for key, future in futures.items():
            if key not in self.work.vectors:
                await asyncio.shield(future)
        return EmbeddingBatchResult(
            vectors=[list(self.work.vectors[key]) for key in keys],
            provider=self.provider_name,
            model=self.model_name,
            dimensions=self.dimensions,
            provider_version=self.provider_version,
            billed_input_tokens=billed,
        )
