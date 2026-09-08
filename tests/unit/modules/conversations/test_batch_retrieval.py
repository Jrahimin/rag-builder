"""Parallel repair has independent sessions, deterministic order, and fail-closed snapshots."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.dependencies.conversations import SearchServiceRetrievalAdapter
from app.modules.conversations.ports import ContextRetrievalResult
from app.platform.providers.errors import ProviderError


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
