"""Unit tests for conversation HTTP helpers."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.api.v1.routes.conversations_router import _sse_error_message, _with_sse_heartbeats
from app.core.exceptions import BadRequestError, ServiceUnavailableError
from app.platform.providers.errors import ProviderError
from app.platform.providers.request_work import RequestWork, current_request_work

pytestmark = pytest.mark.unit


def test_sse_error_message_maps_ape_error() -> None:
    message = _sse_error_message(
        BadRequestError(message="Unknown system prompt version: v9", code="unknown_prompt_version")
    )
    assert message == "Unknown system prompt version: v9"


def test_sse_error_message_sanitizes_provider_error() -> None:
    message = _sse_error_message(ProviderError("internal detail", provider_name="openai"))
    assert message == "The language model provider is temporarily unavailable."
    assert "internal detail" not in message


def test_sse_error_message_sanitizes_unexpected_error() -> None:
    message = _sse_error_message(RuntimeError("db password leaked"))
    assert message == "An unexpected error occurred."
    assert "password" not in message


def test_sse_error_message_maps_service_unavailable() -> None:
    message = _sse_error_message(
        ServiceUnavailableError(
            message="The language model provider is temporarily unavailable.",
            code="llm_provider_unavailable",
        )
    )
    assert "temporarily unavailable" in message


async def test_heartbeat_preserves_pending_generation_and_event_order():
    release = asyncio.Event()
    calls = []

    async def events():
        calls.append("started")
        await release.wait()
        yield 'data: {"event":"done"}\n\n'

    stream = _with_sse_heartbeats(events(), interval=0.001)
    assert await anext(stream) == ": connected\n\n"
    assert await anext(stream) == ": keep-alive\n\n"
    assert await anext(stream) == ": keep-alive\n\n"
    release.set()
    assert await anext(stream) == 'data: {"event":"done"}\n\n'
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert calls == ["started"]


async def test_heartbeat_disconnect_cancels_and_closes_upstream():
    closed = asyncio.Event()

    async def events():
        try:
            await asyncio.Event().wait()
            yield "unreachable"
        finally:
            closed.set()

    stream = _with_sse_heartbeats(events(), interval=0.001)
    await anext(stream)
    await anext(stream)
    await stream.aclose()
    assert closed.is_set()


async def test_heartbeat_uses_one_producer_task_for_every_upstream_advance():
    owners: list[asyncio.Task[object] | None] = []
    observed_work: list[RequestWork | None] = []
    work = RequestWork(uuid.uuid4())

    async def events():
        with work.attached():
            for value in ("one", "two", "three"):
                owners.append(asyncio.current_task())
                observed_work.append(current_request_work())
                yield value
                await asyncio.sleep(0)

    received = [item async for item in _with_sse_heartbeats(events(), interval=0.01)]

    assert received == [": connected\n\n", "one", "two", "three"]
    assert owners[0] is not None
    assert all(owner is owners[0] for owner in owners)
    assert observed_work == [work, work, work]
