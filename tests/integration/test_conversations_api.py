"""Integration tests for conversations API."""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _create_project(client: AsyncClient) -> str:
    response = await client.post(
        "/api/v1/projects",
        json={"name": f"Chat Project {uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 201
    return response.json()["data"]["id"]


async def _create_conversation(client: AsyncClient, project_id: str) -> str:
    response = await client.post(
        f"/api/v1/projects/{project_id}/conversations",
        json={},
    )
    assert response.status_code == 201
    return response.json()["data"]["id"]


async def test_create_conversation_and_send_message(db_client: AsyncClient) -> None:
    project_id = await _create_project(db_client)
    conversation_id = await _create_conversation(db_client, project_id)

    response = await db_client.post(
        f"/api/v1/projects/{project_id}/conversations/{conversation_id}/messages",
        json={"content": "What is APE?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["user_message"]["content"] == "What is APE?"
    assistant = body["data"]["assistant_message"]
    assert assistant["content"] == (
        "I don't have enough evidence in the indexed sources to answer that question."
    )
    assert assistant["grounded"] is False
    assert assistant["insufficient_evidence_reason"] == "no_retrieval_results"
    assert assistant["claims"] == []


async def test_send_message_provider_error_returns_503(db_client: AsyncClient) -> None:
    from app.core.exceptions import ServiceUnavailableError
    from app.dependencies.conversations import get_chat_service

    class _FailingSvc:
        async def send_message(self, conversation_id, request):
            del conversation_id, request
            raise ServiceUnavailableError(
                message="The language model provider is temporarily unavailable.",
                code="llm_provider_unavailable",
            )

    db_client._transport.app.dependency_overrides[get_chat_service] = _FailingSvc

    project_id = await _create_project(db_client)
    conversation_id = await _create_conversation(db_client, project_id)

    response = await db_client.post(
        f"/api/v1/projects/{project_id}/conversations/{conversation_id}/messages",
        json={"content": "fail please"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_provider_unavailable"


async def test_stream_message_emits_sanitized_error_on_failure(
    db_client: AsyncClient,
) -> None:
    from app.core.exceptions import ServiceUnavailableError
    from app.dependencies.conversations import get_chat_service

    class _FailingStreamSvc:
        async def stream_message(self, conversation_id, request, *, should_cancel=None):
            del conversation_id, request, should_cancel
            raise ServiceUnavailableError(
                message="The language model provider is temporarily unavailable.",
                code="llm_provider_unavailable",
            )
            yield ""  # pragma: no cover

    db_client._transport.app.dependency_overrides[get_chat_service] = _FailingStreamSvc

    project_id = await _create_project(db_client)
    conversation_id = await _create_conversation(db_client, project_id)

    async with db_client.stream(
        "POST",
        f"/api/v1/projects/{project_id}/conversations/{conversation_id}/messages/stream",
        json={"content": "stream fail"},
    ) as response:
        assert response.status_code == 200
        payload = ""
        async for chunk in response.aiter_text():
            payload += chunk

    data_lines = [
        line.removeprefix("data: ").strip()
        for line in payload.splitlines()
        if line.startswith("data:")
    ]
    assert data_lines
    event = json.loads(data_lines[-1])
    assert event["event"] == "error"
    assert "temporarily unavailable" in event["message"]
