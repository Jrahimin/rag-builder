"""Provider-neutral work scope, durable exact-input reuse and billing admission."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal
from typing import Any, Protocol
from uuid import UUID

import httpx

from app.core.config import ProviderCostsConfig
from app.platform.providers.contracts.embedding import (
    BaseEmbeddingProvider,
    EmbeddingBatchResult,
    EmbeddingPurpose,
)
from app.platform.providers.errors import ProviderError


class ProviderBudgetError(ProviderError):
    code = "provider_budget_exhausted"


class ProviderWorkStore(Protocol):
    def cache_session(self, project_id: UUID) -> Any: ...
    async def vectors(
        self, session: Any, project_id: UUID, keys: list[str]
    ) -> dict[str, list[float]]: ...
    async def seed_vectors(
        self, session: Any, project_id: UUID, identity: dict[str, Any], inputs: dict[str, str]
    ) -> dict[str, list[float]]: ...
    async def save_vectors(
        self,
        session: Any,
        project_id: UUID,
        rows: dict[str, tuple[dict[str, Any], list[float]]],
        ttl_days: int,
    ) -> None: ...
    async def reserve(
        self, scope: ProviderWorkScope, endpoint: str, model: str, purpose: str, estimate: int
    ) -> UUID: ...
    async def complete(
        self, attempt: UUID, status: str, tokens: int | None, units: int | None, cost: int | None
    ) -> None: ...


@dataclass
class ProviderWorkScope:
    project_id: UUID
    reference: str
    workload: str
    environment: str
    embedding_set_version: int
    config: ProviderCostsConfig
    store: ProviderWorkStore
    counts: dict[str, int] = field(default_factory=dict)


_scope: ContextVar[ProviderWorkScope | None] = ContextVar("provider_work_scope", default=None)


def current_provider_scope() -> ProviderWorkScope | None:
    return _scope.get()


@contextmanager
def attached_provider_scope(scope: ProviderWorkScope) -> Iterator[None]:
    token = _scope.set(scope)
    try:
        yield
    finally:
        _scope.reset(token)


def micro_usd(value: Decimal) -> int:
    return int((value * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


def embedding_reservation(texts: list[str], config: ProviderCostsConfig) -> int:
    # Allow tokenizer framing per input in addition to a conservative byte proxy.
    size = sum(len(text.encode()) + 32 for text in texts)
    return micro_usd(Decimal(size) * Decimal(str(config.embedding_usd_per_million)) / 1_000_000)


def billed_usage(payload: object) -> tuple[int | None, int | None]:
    if not isinstance(payload, dict):
        return None, None
    meta = payload.get("meta", {})
    billed = meta.get("billed_units", {}) if isinstance(meta, dict) else {}
    if not isinstance(billed, dict):
        return None, None

    def unit(name: str) -> int | None:
        value = billed.get(name)
        return (
            value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        )

    return unit("input_tokens"), unit("search_units")


async def metered_cohere_post(
    send: Callable[[], Awaitable[httpx.Response]], endpoint: str, payload: dict[str, Any]
) -> httpx.Response:
    scope = current_provider_scope()
    if scope is None:
        from app.core.config import get_settings

        if get_settings().provider_costs.enabled:
            raise ProviderError(
                "Provider accounting requires explicit project/workload scope",
                provider_name="cohere",
            )
        return await send()
    if scope.workload == "evaluation" and (
        not scope.config.paid_evaluation_enabled or not scope.config.enforce_budgets
    ):
        raise ProviderError(
            "Paid evaluation requires explicit opt-in and enforced finite budgets",
            provider_name="cohere",
        )
    if not scope.config.enabled:
        return await send()
    cfg = scope.config
    purpose = str(payload.get("input_type", "reranking"))
    model = str(payload.get("model", "unknown"))
    if cfg.enforce_budgets and (endpoint, model) not in {
        ("/v2/embed", "embed-v4.0"),
        ("/v2/rerank", "rerank-v4.0-pro"),
    }:
        raise ProviderError("No approved price for this provider model", provider_name="cohere")
    if endpoint == "/v2/embed":
        estimate = embedding_reservation(payload.get("texts", []), cfg)
    else:
        query_bytes = len(str(payload.get("query", "")).encode())
        virtual_docs = sum(
            max(1, (len(t.encode()) + query_bytes + 499) // 500)
            for t in payload.get("documents", [])
        )
        estimate = micro_usd(
            Decimal(max(1, (virtual_docs + 99) // 100)) * Decimal(str(cfg.rerank_usd_per_unit))
        )
    attempt = await scope.store.reserve(scope, endpoint, model, purpose, estimate)
    status = "unknown"
    tokens = units = cost = None
    try:
        response = await send()
        status = "completed" if not response.is_error else "http_error"
        with suppress(ValueError, TypeError):
            tokens, units = billed_usage(response.json())
        known = tokens if endpoint == "/v2/embed" else units
        if known is not None and model in {"embed-v4.0", "rerank-v4.0-pro"}:
            rate = (
                Decimal(str(cfg.embedding_usd_per_million)) / 1_000_000
                if endpoint == "/v2/embed"
                else Decimal(str(cfg.rerank_usd_per_unit))
            )
            cost = micro_usd(Decimal(known) * rate)
        return response
    finally:
        # Even malformed responses, timeouts and cancelled turns retain a billing attempt.
        await asyncio.shield(scope.store.complete(attempt, status, tokens, units, cost))


def cache_identity(
    provider: BaseEmbeddingProvider, embedding_set_version: int, purpose: EmbeddingPurpose
) -> dict[str, Any]:
    return {
        "provider": provider.provider_name,
        "model": provider.model_name,
        "dimensions": provider.dimensions,
        "provider_version": provider.provider_version,
        "purpose": purpose.value,
        "embedding_set_version": embedding_set_version,
        "namespace": provider.cache_namespace,
        "schema": 1,
    }


def cache_key(identity: dict[str, Any], text: str) -> str:
    return hashlib.sha256(
        json.dumps(
            {**identity, "input_hash": hashlib.sha256(text.encode()).hexdigest()}, sort_keys=True
        ).encode()
    ).hexdigest()


def validate_vectors(vectors: list[list[float]], dimensions: int, provider: str) -> None:
    if any(len(v) != dimensions or any(not math.isfinite(x) for x in v) for v in vectors):
        raise ProviderError("Embedding cache received invalid vectors", provider_name=provider)


class CachedEmbeddingProvider(BaseEmbeddingProvider):
    """Cache exact inputs only; no answer/evidence-result caching."""

    def __init__(self, provider: BaseEmbeddingProvider) -> None:
        self.provider = provider

    @property
    def cache_namespace(self) -> str:
        return self.provider.cache_namespace

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
        scope = current_provider_scope()
        if (
            scope is None
            or not scope.config.cache_enabled
            or self.provider_name == "hash"
            or not texts
        ):
            return await self.provider.embed_texts(texts, purpose=purpose)
        # Commit each vendor-sized batch independently: a later failure cannot erase paid work.
        vectors: list[list[float]] = []
        billed: int | None = 0
        for offset in range(0, len(texts), 96):
            batch = texts[offset : offset + 96]
            identity = cache_identity(self, scope.embedding_set_version, purpose)
            keys = [cache_key(identity, text) for text in batch]
            async with scope.store.cache_session(scope.project_id) as session:
                found = await scope.store.vectors(session, scope.project_id, keys)
                missing = dict(zip(keys, batch, strict=True))
                missing = {key: text for key, text in missing.items() if key not in found}
                if missing:
                    seeded = await scope.store.seed_vectors(
                        session,
                        scope.project_id,
                        identity,
                        {
                            key: hashlib.sha256(text.encode()).hexdigest()
                            for key, text in missing.items()
                        },
                    )
                    validate_vectors(list(seeded.values()), self.dimensions, self.provider_name)
                    found.update(seeded)
                    await scope.store.save_vectors(
                        session,
                        scope.project_id,
                        {
                            key: (
                                {
                                    **identity,
                                    "input_hash": hashlib.sha256(missing[key].encode()).hexdigest(),
                                },
                                vector,
                            )
                            for key, vector in seeded.items()
                        },
                        scope.config.cache_ttl_days,
                    )
                    missing = {key: text for key, text in missing.items() if key not in found}
                scope.counts["cache_hits"] = (
                    scope.counts.get("cache_hits", 0) + len(keys) - len(missing)
                )
                if missing:
                    result = await self.provider.embed_texts(
                        list(missing.values()), purpose=purpose
                    )
                    if (
                        len(result.vectors) != len(missing)
                        or result.provider != self.provider_name
                        or result.model != self.model_name
                        or result.dimensions != self.dimensions
                        or result.provider_version != self.provider_version
                    ):
                        raise ProviderError(
                            "Embedding cache fill returned an invalid batch",
                            provider_name=self.provider_name,
                        )
                    validate_vectors(result.vectors, self.dimensions, self.provider_name)
                    found.update(zip(missing, result.vectors, strict=True))
                    rows = {
                        key: (
                            {**identity, "input_hash": hashlib.sha256(text.encode()).hexdigest()},
                            found[key],
                        )
                        for key, text in missing.items()
                    }
                    await scope.store.save_vectors(
                        session, scope.project_id, rows, scope.config.cache_ttl_days
                    )
                    billed = (
                        None
                        if billed is None or result.billed_input_tokens is None
                        else billed + result.billed_input_tokens
                    )
                validate_vectors(list(found.values()), self.dimensions, self.provider_name)
                vectors.extend(found[key] for key in keys)
        return EmbeddingBatchResult(
            vectors=vectors,
            provider=self.provider_name,
            model=self.model_name,
            dimensions=self.dimensions,
            provider_version=self.provider_version,
            billed_input_tokens=billed,
        )
