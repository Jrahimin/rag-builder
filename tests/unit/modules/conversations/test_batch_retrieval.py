"""Parallel repair has independent sessions, deterministic order, and fail-closed snapshots."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.dependencies.conversations import SearchServiceRetrievalAdapter
from app.modules.conversations.ports import ContextRetrievalResult
from app.platform.providers.contracts.embedding import EmbeddingPurpose
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.hash_embedding import HashEmbeddingProvider
from app.platform.providers.request_work import RequestWork


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "strategy,expected_calls", [("hybrid", 1), ("semantic", 1), ("keyword", 2)]
)
async def test_recovery_batches_exact_queries_without_skipping_search(
    monkeypatch, strategy, expected_calls
):
    provider = HashEmbeddingProvider(dimensions=8, model="hash", provider_version="1")
    provider.embed_texts = AsyncMock(wraps=provider.embed_texts)
    work = RequestWork(uuid.uuid4())
    cached = work.wrap(provider)
    service = SimpleNamespace(resolved_query_embedder=cached)
    snapshot = {"strategy": strategy, "index_build_id": "build"}
    searches = []

    @asynccontextmanager
    async def sessions():
        yield object()

    async def retrieve(adapter, **request):
        searches.append(request)
        await cached.embed_texts([request["query"]], purpose=EmbeddingPurpose.QUERY)
        return ContextRetrievalResult([], snapshot)

    monkeypatch.setattr(SearchServiceRetrievalAdapter, "retrieve", retrieve)
    adapter = SearchServiceRetrievalAdapter(
        service, session_factory=sessions, branch_factory=lambda session, pinned: service
    )
    requests = [{"query": "salary"}, {"query": "interest"}, {"query": "salary"}]
    await adapter.retrieve_batch(requests, snapshot=snapshot)
    assert searches == requests
    assert provider.embed_texts.await_count == expected_calls
    assert all(
        call.kwargs["purpose"] is EmbeddingPurpose.QUERY
        for call in provider.embed_texts.call_args_list
    )
    assert work.counts["recovery_query_embedding_batches"] == (strategy != "keyword")


@pytest.mark.asyncio
async def test_failed_embedding_warmup_never_starts_branches_or_caches_vectors():
    provider = HashEmbeddingProvider(dimensions=8, model="hash", provider_version="1")
    provider.embed_texts = AsyncMock(side_effect=ProviderError("Unavailable"))
    work = RequestWork(uuid.uuid4())
    cached = work.wrap(provider)
    factory = AsyncMock()
    adapter = SearchServiceRetrievalAdapter(
        SimpleNamespace(resolved_query_embedder=cached),
        session_factory=factory,
        branch_factory=lambda session, pinned: object(),
    )
    with pytest.raises(ProviderError):
        await adapter.retrieve_batch([{"query": "salary"}], snapshot={"strategy": "hybrid"})
    factory.assert_not_called()
    assert not work.vectors
    assert work.counts["recovery_query_embedding_batches"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    ["index_build_id", "source_metadata_generation", "configuration_hash", "reference_date"],
)
async def test_source_activation_and_other_snapshot_changes_are_rejected(monkeypatch, changed):
    snapshot = {
        "index_build_id": "build",
        "source_metadata_generation": 7,
        "configuration_hash": "config",
        "reference_date": "2026-09-08",
    }

    @asynccontextmanager
    async def sessions():
        yield object()

    async def retrieve(adapter, **request):
        return ContextRetrievalResult([], {**snapshot, changed: "changed"})

    monkeypatch.setattr(SearchServiceRetrievalAdapter, "retrieve", retrieve)
    adapter = SearchServiceRetrievalAdapter(
        object(), session_factory=sessions, branch_factory=lambda session, pinned: object()
    )
    with pytest.raises(ProviderError) as error:
        await adapter.retrieve_batch([{"query": "question"}], snapshot=snapshot)
    assert error.value.context["reason"] == f"snapshot_mismatch_{changed}"


@pytest.mark.asyncio
async def test_batch_owns_sessions_bounds_parallelism_and_preserves_order(monkeypatch):
    active = maximum = 0
    opened, closed = [], []
    snapshot = {
        "index_build_id": "build",
        "source_metadata_generation": 7,
        "configuration_hash": "config",
        "reference_date": "2026-09-08",
    }

    @asynccontextmanager
    async def sessions():
        session = object()
        opened.append(session)
        try:
            yield session
        finally:
            closed.append(session)

    async def retrieve(adapter, **request):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(0.01)
            return ContextRetrievalResult(
                [], {**adapter._search_service.snapshot, "query": request["query"]}
            )
        finally:
            active -= 1

    monkeypatch.setattr(SearchServiceRetrievalAdapter, "retrieve", retrieve)
    adapter = SearchServiceRetrievalAdapter(
        object(),
        session_factory=sessions,
        branch_factory=lambda session, pinned: SimpleNamespace(session=session, snapshot=pinned),
    )
    results = await adapter.retrieve_batch([{"query": str(i)} for i in range(8)], snapshot=snapshot)
    assert [r.diagnostics["query"] for r in results] == list(map(str, range(8)))
    assert maximum == 3
    assert len(set(opened)) == 8 and set(opened) == set(closed)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["snapshot", "provider", "cancel"])
async def test_batch_failure_and_cancellation_close_all_sessions(monkeypatch, failure):
    closed = []
    started = asyncio.Event()

    @asynccontextmanager
    async def sessions():
        try:
            yield object()
        finally:
            closed.append(True)

    async def retrieve(adapter, **request):
        started.set()
        if failure == "cancel":
            await asyncio.Event().wait()
        if request["query"] == "bad":
            if failure == "provider":
                raise ProviderError("Unavailable")
            return ContextRetrievalResult([], {"index_build_id": "wrong"})
        await asyncio.Event().wait()

    monkeypatch.setattr(SearchServiceRetrievalAdapter, "retrieve", retrieve)
    adapter = SearchServiceRetrievalAdapter(
        object(), session_factory=sessions, branch_factory=lambda session, pinned: object()
    )
    task = asyncio.create_task(
        adapter.retrieve_batch(
            [{"query": "bad"}, {"query": "wait"}], snapshot={"index_build_id": "expected"}
        )
    )
    await started.wait()
    if failure == "cancel":
        task.cancel()
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else ProviderError):
        await task
    assert len(closed) == 2
