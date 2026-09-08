"""Turn-local provider work reuse and aggregate telemetry (never an answer cache)."""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager
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
from app.platform.providers.errors import ProviderError
from app.platform.providers.prompt_budget import prompt_budget

_AsyncMethod = TypeVar("_AsyncMethod", bound=Callable[..., Awaitable[Any]])


def observe_stage(name: str) -> Callable[[_AsyncMethod], _AsyncMethod]:
    """Instrument service boundaries without retaining inputs or candidate text."""

    def decorate(method: _AsyncMethod) -> _AsyncMethod:
        @wraps(method)
        async def observed(self: Any, *args: Any, **kwargs: Any) -> Any:
            work = getattr(self, "_work", None) or getattr(
                getattr(self, "_embedder", None), "work", None
            )
            if not isinstance(work, RequestWork):
                return await method(self, *args, **kwargs)
            with work.stage(name):
                return await method(self, *args, **kwargs)

        return cast(_AsyncMethod, observed)

    return decorate


class RequestWork:
    """One instance per turn, shared only by that turn's retrieval branches."""

    def __init__(self, project_id: uuid.UUID) -> None:
        self.project_id = project_id
        self.started = time.perf_counter()
        self.timings: Counter[str] = Counter()
        self.counts: Counter[str] = Counter()
        self.vectors: dict[tuple[object, ...], list[float]] = {}
        self.content: dict[tuple[object, ...], object] = {}
        self.embedding_lock = asyncio.Lock()
        self.calls: list[dict[str, Any]] = []

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.timings[name] += round((time.perf_counter() - started) * 1000)

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": "turn.v1",
            "processing_ms": round((time.perf_counter() - self.started) * 1000),
            "stages_ms": dict(self.timings),
            "counts": dict(self.counts),
            "provider_calls": list(self.calls),
            "stage_semantics": "elapsed stage durations may overlap across parallel branches",
        }

    def wrap(self, provider: BaseEmbeddingProvider) -> BaseEmbeddingProvider:
        if isinstance(provider, CachedEmbeddingProvider) and provider.work is self:
            return provider
        return CachedEmbeddingProvider(provider, self)


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
    ) -> AsyncIterator[ChatCompletionChunk]:
        self._check_budget(messages, max_tokens)
        with self._call() as call:
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

    @contextmanager
    def _call(self) -> Iterator[dict[str, Any]]:
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
        try:
            yield call
        finally:
            call["duration_ms"] = round((time.perf_counter() - started) * 1000)
            self.work.calls.append(call)


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
                    self.work.calls.append(call)
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
