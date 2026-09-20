"""Connection telemetry observes real pool checkout signals without forcing work."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.db.session import ObservedAsyncSession, _record_pool_checkout
from app.platform.providers.request_work import RequestWork

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_connection_records_only_an_actual_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()

    async def fake_connection(self: AsyncSession, **kwargs: object) -> object:
        del self, kwargs
        _record_pool_checkout()
        return sentinel

    monkeypatch.setattr(AsyncSession, "connection", fake_connection)
    work = RequestWork(uuid.uuid4())
    session = ObservedAsyncSession.__new__(ObservedAsyncSession)
    with work.attached():
        result = await ObservedAsyncSession.connection(session)
    assert result is sentinel
    assert work.counts["database_connection_acquisition_waits"] == 1


@pytest.mark.asyncio
async def test_execute_does_not_preacquire_or_count_cached_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquired = AsyncMock(return_value=object())
    executed = AsyncMock(return_value="ok")
    monkeypatch.setattr(AsyncSession, "connection", acquired)
    monkeypatch.setattr(AsyncSession, "execute", executed)
    work = RequestWork(uuid.uuid4())
    session = ObservedAsyncSession.__new__(ObservedAsyncSession)
    with work.attached():
        result = await ObservedAsyncSession.execute(session, "select 1")
    assert result == "ok"
    acquired.assert_not_awaited()
    assert work.counts["database_connection_acquisition_waits"] == 0


@pytest.mark.asyncio
async def test_execute_records_checkout_reported_inside_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def execute(*_args: object, **_kwargs: object) -> str:
        _record_pool_checkout()
        return "ok"

    monkeypatch.setattr(AsyncSession, "execute", execute)
    work = RequestWork(uuid.uuid4())
    session = ObservedAsyncSession.__new__(ObservedAsyncSession)
    with work.attached():
        await ObservedAsyncSession.execute(session, "select 1")
    assert work.counts["database_connection_acquisition_waits"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get", "stream", "stream_scalars", "flush", "commit"])
async def test_implicit_acquisition_paths_are_probed(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    async def operation(*_args: object, **_kwargs: object) -> object:
        _record_pool_checkout()
        return object()

    monkeypatch.setattr(AsyncSession, method, operation)
    work = RequestWork(uuid.uuid4())
    session = ObservedAsyncSession.__new__(ObservedAsyncSession)
    args = (object(),) if method == "get" else ()
    if method in {"stream", "stream_scalars"}:
        args = ("select 1",)
    with work.attached():
        await getattr(ObservedAsyncSession, method)(session, *args)
    assert work.counts["database_connection_acquisition_waits"] == 1


@pytest.mark.asyncio
async def test_pool_timeout_is_recorded_as_failed_acquisition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def timed_out(*_args: object, **_kwargs: object) -> object:
        raise SQLAlchemyTimeoutError("pool exhausted")

    monkeypatch.setattr(AsyncSession, "execute", timed_out)
    work = RequestWork(uuid.uuid4())
    session = ObservedAsyncSession.__new__(ObservedAsyncSession)
    with work.attached(), pytest.raises(SQLAlchemyTimeoutError):
        await ObservedAsyncSession.execute(session, "select 1")
    assert work.counts["database_connection_acquisition_waits"] == 1
    spans = work.snapshot()["spans"]["items"]
    assert spans[-1]["outcome"] == "failed"


@pytest.mark.asyncio
async def test_empty_flush_does_not_force_or_record_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flushed = AsyncMock(return_value=None)
    monkeypatch.setattr(AsyncSession, "flush", flushed)
    work = RequestWork(uuid.uuid4())
    session = ObservedAsyncSession.__new__(ObservedAsyncSession)
    with work.attached():
        await ObservedAsyncSession.flush(session)
    flushed.assert_awaited_once_with(None)
    assert work.counts["database_connection_acquisition_waits"] == 0
