"""Expense controls preserve exact embeddings and fail before paid dispatch."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import ProviderCostsConfig
from app.platform.providers.contracts.embedding import (
    BaseEmbeddingProvider,
    EmbeddingBatchResult,
    EmbeddingPurpose,
)
from app.platform.providers.errors import ProviderError
from app.platform.providers.provider_work import (
    CachedEmbeddingProvider,
    ProviderBudgetError,
    ProviderWorkScope,
    attached_provider_scope,
    billed_usage,
    current_provider_scope,
    metered_cohere_post,
)

pytestmark = pytest.mark.unit


class MemoryStore:
    def __init__(self):
        self.data = {}
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def cache_session(self, project):
        async with self.lock:
            transaction = dict(self.data)
            yield transaction
            self.data = transaction

    async def vectors(self, session, project, keys):
        return {key: session[project, key] for key in keys if (project, key) in session}

    async def seed_vectors(self, *args):
        return {}

    async def save_vectors(self, session, project, rows, ttl):
        session.update({(project, key): vector for key, (_, vector) in rows.items()})


class Embeddings(BaseEmbeddingProvider):
    provider_name = "cohere"
    model_name = "embed-v4.0"
    dimensions = 2
    provider_version = "v1"
    cache_namespace = "https://api.cohere.com"

    def __init__(self):
        self.calls = []
        self.fail_call = None

    async def embed_texts(self, texts, *, purpose=EmbeddingPurpose.DOCUMENT):
        self.calls.append((texts, purpose))
        await asyncio.sleep(0)
        if len(self.calls) == self.fail_call:
            raise ProviderError("timeout", provider_name="cohere")
        return EmbeddingBatchResult(
            [[float(len(t)), 1.0] for t in texts],
            self.provider_name,
            self.model_name,
            self.dimensions,
            self.provider_version,
            len(texts) * 10,
        )


def work(store=None, **config):
    return ProviderWorkScope(
        uuid.uuid4(),
        str(uuid.uuid4()),
        "chat",
        "testing",
        3,
        ProviderCostsConfig(enabled=True, cache_enabled=True, **config),
        store or MemoryStore(),
    )


async def test_repeated_build_and_new_document_pay_only_for_unique_new_inputs():
    raw = Embeddings()
    provider = CachedEmbeddingProvider(raw)
    scope = work()
    with attached_provider_scope(scope):
        first = await provider.embed_texts(["a", "b", "a"])
        second = await provider.embed_texts(["b", "a"])
        third = await provider.embed_texts(["a", "new"])
    assert raw.calls == [
        (["a", "b"], EmbeddingPurpose.DOCUMENT),
        (["new"], EmbeddingPurpose.DOCUMENT),
    ]
    assert first.vectors == [[1.0, 1.0]] * 3
    assert second.billed_input_tokens == 0
    assert third.billed_input_tokens == 10
    assert current_provider_scope() is None


@pytest.mark.parametrize(
    "change", ["purpose", "project", "version", "endpoint", "model", "dimensions", "adapter"]
)
async def test_cache_identity_separates_incompatible_work(change):
    raw = Embeddings()
    provider = CachedEmbeddingProvider(raw)
    scope = work()
    with attached_provider_scope(scope):
        await provider.embed_texts(["a"])
    next_scope = scope
    purpose = EmbeddingPurpose.DOCUMENT
    if change == "purpose":
        purpose = EmbeddingPurpose.QUERY
    elif change == "project":
        next_scope = replace(scope, project_id=uuid.uuid4())
    elif change == "version":
        next_scope = replace(scope, embedding_set_version=4)
    elif change == "endpoint":
        raw.cache_namespace = "https://other.example"
    elif change == "model":
        raw.model_name = "other-model"
    elif change == "dimensions":
        # Changing advertised dimensions while returning two values is rejected, never cached.
        raw.dimensions = 3
    else:
        raw.provider_version = "v2"
    with attached_provider_scope(next_scope):
        if change == "dimensions":
            with pytest.raises(ProviderError):
                await provider.embed_texts(["a"], purpose=purpose)
        else:
            await provider.embed_texts(["a"], purpose=purpose)
    assert len(raw.calls) == 2


async def test_concurrent_turns_pay_once_for_identical_passages():
    raw = Embeddings()
    provider = CachedEmbeddingProvider(raw)
    with attached_provider_scope(work()):
        results = await asyncio.gather(*(provider.embed_texts(["shared"]) for _ in range(6)))
    assert len(raw.calls) == 1
    assert all(result.vectors == results[0].vectors for result in results)


async def test_partial_build_failure_commits_completed_batches_for_retry():
    raw = Embeddings()
    raw.fail_call = 2
    provider = CachedEmbeddingProvider(raw)
    texts = [str(n) for n in range(97)]
    with attached_provider_scope(work()):
        with pytest.raises(ProviderError):
            await provider.embed_texts(texts)
        raw.fail_call = None
        result = await provider.embed_texts(texts)
    assert [len(call[0]) for call in raw.calls] == [96, 1, 1]
    assert len(result.vectors) == 97
    assert result.billed_input_tokens == 10


@pytest.mark.parametrize(
    "endpoint,payload,billed,cost",
    [
        (
            "/v2/embed",
            {"model": "embed-v4.0", "texts": ["abc"], "input_type": "search_document"},
            {"input_tokens": 1000},
            120,
        ),
        (
            "/v2/rerank",
            {"model": "rerank-v4.0-pro", "query": "q", "documents": ["doc"]},
            {"search_units": 2},
            5000,
        ),
    ],
)
async def test_meter_uses_actual_billed_units(endpoint, payload, billed, cost):
    store = AsyncMock()
    attempt = uuid.uuid4()
    store.reserve.return_value = attempt
    response = httpx.Response(200, json={"meta": {"billed_units": billed}})
    send = AsyncMock(return_value=response)
    with attached_provider_scope(work(store)):
        assert await metered_cohere_post(send, endpoint, payload) is response
    store.reserve.assert_awaited_once()
    store.complete.assert_awaited_once_with(
        attempt, "completed", billed.get("input_tokens"), billed.get("search_units"), cost
    )


async def test_budget_rejection_never_dispatches():
    store = AsyncMock()
    store.reserve.side_effect = ProviderBudgetError("exhausted", provider_name="cohere")
    send = AsyncMock()
    with (
        attached_provider_scope(work(store, enforce_budgets=True)),
        pytest.raises(ProviderBudgetError),
    ):
        await metered_cohere_post(send, "/v2/embed", {"model": "embed-v4.0", "texts": ["x"]})
    send.assert_not_awaited()
    store.complete.assert_not_awaited()


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("uncertain"), asyncio.CancelledError()])
async def test_uncertain_attempts_retain_reservation_even_on_cancellation(failure):
    store = AsyncMock()
    attempt = uuid.uuid4()
    store.reserve.return_value = attempt
    with attached_provider_scope(work(store)), pytest.raises(type(failure)):
        await metered_cohere_post(
            AsyncMock(side_effect=failure), "/v2/embed", {"model": "embed-v4.0", "texts": ["x"]}
        )
    store.complete.assert_awaited_once_with(attempt, "unknown", None, None, None)


async def test_paid_evaluation_requires_opt_in_before_reservation():
    store = AsyncMock()
    send = AsyncMock()
    with (
        attached_provider_scope(replace(work(store), workload="evaluation")),
        pytest.raises(ProviderError, match="opt-in"),
    ):
        await metered_cohere_post(send, "/v2/embed", {"model": "embed-v4.0", "texts": ["x"]})
    store.reserve.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"meta": {"billed_units": {"input_tokens": True}}},
        {"meta": {"billed_units": {"input_tokens": -1}}},
    ],
)
def test_unknown_billing_is_not_zero(payload):
    assert billed_usage(payload) == (None, None)


@pytest.mark.parametrize("amount", [float("inf"), float("nan"), 0, -1])
def test_budgets_must_be_finite_positive(amount):
    with pytest.raises(ValidationError):
        ProviderCostsConfig(monthly_budget_usd=amount)


async def test_ordinary_suite_cannot_send_external_http():
    async with httpx.AsyncClient() as client:
        with pytest.raises(AssertionError, match="Ordinary tests"):
            await client.get("https://api.cohere.com/anything")
