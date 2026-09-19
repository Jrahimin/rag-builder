"""Turn-local provider work reuse and aggregate telemetry (never an answer cache)."""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections import Counter
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
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
)
from app.platform.providers.errors import ProviderError, sanitized_provider_failure_reason
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
        self.embedding_lock = asyncio.Lock()
        self.calls: list[dict[str, Any]] = []
        self.validation_retries: list[dict[str, Any]] = []
        self.max_span_detail = max(1, max_span_detail)
        self._spans: list[dict[str, Any]] = []
        self._omitted_spans = 0
        self._active: Counter[str] = Counter()
        self._open: dict[str, dict[str, Any]] = {}
        self._span_started: dict[str, float] = {}
        self._unbound_purpose: str | None = None

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
        self, messages: list[ChatMessage], *, temperature: float | None = None, max_tokens: int
    ) -> ChatCompletionResult:
        self._check_budget(messages, max_tokens)
        with self._call() as call:
            result = await self.provider.generate(
                messages, temperature=temperature, max_tokens=max_tokens
            )
            call.update(
                status="completed",
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                reasoning_tokens=result.usage.reasoning_tokens,
            )
            return result

    async def stream(
        self, messages: list[ChatMessage], *, temperature: float | None = None, max_tokens: int
    ) -> AsyncGenerator[ChatCompletionChunk, None]:
        self._check_budget(messages, max_tokens)
        call, started = self._open_call()
        try:
            async for chunk in self.provider.stream(
                messages, temperature=temperature, max_tokens=max_tokens
            ):
                if chunk.usage is not None:
                    call.update(
                        input_tokens=chunk.usage.input_tokens,
                        output_tokens=chunk.usage.output_tokens,
                        reasoning_tokens=chunk.usage.reasoning_tokens,
                    )
                yield chunk
            call["status"] = "completed"
        except (asyncio.CancelledError, GeneratorExit):
            call["status"] = "cancelled"
            raise
        finally:
            call["duration_ms"] = round((time.perf_counter() - started) * 1000)

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
        prefix = (
            self.work.project_id,
            self.provider_name,
            self.model_name,
            self.provider_version,
            self.dimensions,
            purpose.value,
        )
        keys = [(*prefix, hashlib.sha256(text.encode("utf-8")).hexdigest()) for text in texts]
        # Serialize the miss check and fill to coalesce overlapping concurrent requests.
        # Failed/cancelled calls never populate the cache; the lock releases on cancellation.
        async with self.work.embedding_lock:
            missing = {
                key: text
                for key, text in zip(keys, texts, strict=True)
                if key not in self.work.vectors
            }
            self.work.counts["embedding_cache_hits"] += len(keys) - len(missing)
            if missing:
                started = time.perf_counter()
                call: dict[str, Any] = {
                    "kind": "embedding",
                    "provider": self.provider_name,
                    "model": self.model_name,
                    "purpose": purpose.value,
                    "texts": len(missing),
                    "input_tokens": None,
                    "output_tokens": None,
                    "status": "failed",
                    "provider_internal_retries": None,
                }
                self.work.counts["embedding_calls"] += 1
                self.work.counts["embedded_texts"] += len(missing)
                self.work.annotate_provider_call(call, purpose_field="work_purpose")
                try:
                    result = await self.provider.embed_texts(
                        list(missing.values()), purpose=purpose
                    )
                    if (
                        (result.provider, result.model, result.dimensions, result.provider_version)
                        != (
                            self.provider_name,
                            self.model_name,
                            self.dimensions,
                            self.provider_version,
                        )
                        or len(result.vectors) != len(missing)
                        or any(len(vector) != self.dimensions for vector in result.vectors)
                    ):
                        raise ProviderError(
                            "Embedding result identity or vector shape mismatch",
                            provider_name=self.provider_name,
                            context={"reason": "embedding_identity_mismatch"},
                        )
                    self.work.vectors.update(zip(missing, result.vectors, strict=True))
                    call["status"] = "completed"
                finally:
                    call["duration_ms"] = round((time.perf_counter() - started) * 1000)
                    self.work.timings[
                        "query_embedding"
                        if purpose is EmbeddingPurpose.QUERY
                        else "document_embedding"
                    ] += call["duration_ms"]
        return EmbeddingBatchResult(
            vectors=[list(self.work.vectors[key]) for key in keys],
            provider=self.provider_name,
            model=self.model_name,
            dimensions=self.dimensions,
            provider_version=self.provider_version,
        )
