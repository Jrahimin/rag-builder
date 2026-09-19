"""Connection-acquisition instrumentation records success as well as failure."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.db.session import ObservedAsyncSession
from app.platform.providers.request_work import RequestWork

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_successful_connection_records_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()

    async def fake_connection(self: AsyncSession, **kwargs: object) -> object:
        del self, kwargs
        return sentinel

    monkeypatch.setattr(AsyncSession, "connection", fake_connection)
    work = RequestWork(uuid.uuid4())
    session = ObservedAsyncSession.__new__(ObservedAsyncSession)
    session.in_transaction = lambda: False  # type: ignore[method-assign]
    session.in_nested_transaction = lambda: False  # type: ignore[method-assign]
    with work.attached():
        result = await ObservedAsyncSession.connection(session)
    assert result is sentinel
    assert work.counts["database_connection_acquisition_waits"] == 1
    assert "database_connection_acquisition" in work.timings


@pytest.mark.asyncio
async def test_execute_without_transaction_acquires_through_instrumented_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquired = AsyncMock(return_value=object())
    monkeypatch.setattr(AsyncSession, "connection", acquired)
    executed = AsyncMock(return_value="ok")
    monkeypatch.setattr(AsyncSession, "execute", executed)
    work = RequestWork(uuid.uuid4())
    session = ObservedAsyncSession.__new__(ObservedAsyncSession)
    session.in_transaction = lambda: False  # type: ignore[method-assign]
    session.in_nested_transaction = lambda: False  # type: ignore[method-assign]
    with work.attached():
        result = await ObservedAsyncSession.execute(session, "select 1")
    assert result == "ok"
    acquired.assert_awaited()
    assert work.counts["database_connection_acquisition_waits"] == 1
