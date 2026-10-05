"""Actual adapter HTTP wire plus local verification and durable Message projection, offline."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ChatConfig
from app.models.document_chunk import DocumentChunk
from app.models.message import Message, MessageRole
from app.modules.conversations.answer_draft import AnswerDraft, render_verified_segments
from app.modules.conversations.grounding_service import GroundingService
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.repositories.message_repository import MessageRepository
from app.platform.domain.content_hash import content_hash
from app.platform.providers.capabilities import endpoint_identity
from app.platform.providers.contracts.llm import ChatMessage, ChatRole
from app.platform.providers.implementations.openai_compatible_chat import (
    OpenAICompatibleChatProvider,
)
from app.platform.providers.request_work import ObservedLLM, RequestWork
from tests.integration.test_index_lifecycle_api import _ready_document

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("certified", [True, False])
async def test_production_adapter_wire_provenance_survives_verified_message_storage(
    db_client, integration_connection, captured_jobs, monkeypatch, certified
):
    project, document = await _ready_document(db_client, integration_connection, captured_jobs)
    path = f"/api/v1/projects/{project}/conversations"
    conversation = (await db_client.post(path, json={})).json()["data"]["id"]
    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        source = await session.scalar(
            select(DocumentChunk).where(DocumentChunk.document_id == uuid.UUID(document))
        )
        assert source is not None
        draft_json = {
            "segments": [
                {
                    "assertion_id": "A1",
                    "text": source.content,
                    "requirement_ids": [],
                    "proof_ids": [str(source.id)],
                }
            ]
        }
        endpoint = "https://controlled.test"
        declarations = (
            [
                {
                    "provider": "openai",
                    "model": "gpt-6-luna",
                    "endpoint_hash": endpoint_identity(endpoint),
                    "schema_mode": "json_schema",
                    "reviewer": "offline-controlled-wire",
                    "evidence_hash": "a" * 64,
                    "capability_revision": "wire.fixture.v1",
                }
            ]
            if certified
            else []
        )
        provider = OpenAICompatibleChatProvider(
            provider_name="openai",
            api_key="fixture-not-live",
            base_url=endpoint,
            model="gpt-6-luna",
            provider_version="fixture",
            request_timeout_seconds=1,
            schema_capabilities=declarations,
        )
        bodies = []

        def respond(request):
            assert request.url.host == "controlled.test"
            bodies.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": json.dumps(draft_json)}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 12},
                },
            )

        original_client = httpx.AsyncClient
        with monkeypatch.context() as scoped:
            scoped.setattr(
                "app.platform.providers.implementations.openai_compatible_chat.httpx.AsyncClient",
                lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
            )
            work = RequestWork(uuid.uuid4())
            with work.stage("verify", purpose="claim_verification"):
                result = await ObservedLLM(provider, work).generate(
                    [ChatMessage(ChatRole.USER, "Controlled documentary quotation")],
                    max_tokens=256,
                    output_contract=AnswerDraft.contract(),
                )
        assert len(bodies) == 1 and bodies[0]["reasoning_effort"] == "low"
        if certified:
            assert (
                bodies[0]["response_format"]["json_schema"]["schema"]
                == AnswerDraft.contract().schema
            )
        else:
            assert "response_format" not in bodies[0]
            assert any("segments" in message["content"] for message in bodies[0]["messages"])
        parsed = AnswerDraft.model_validate_json(result.content)
        rows = [segment.model_dump(mode="json") for segment in parsed.segments]
        content = render_verified_segments(
            rows, supported_ids={"A1"}, proof_indexes={str(source.id): 1}
        )
        chunk = ContextChunk(
            chunk_id=source.id,
            document_id=source.document_id,
            chunk_index=source.chunk_index,
            content=source.content,
            score=1,
            filename="fixture",
            chunk_hash=content_hash(source.content),
            metadata=source.chunk_metadata,
        )
        proof = await GroundingService(ChatConfig()).map_claims(
            content, [chunk], draft_segments=rows
        )
        assert proof.grounded is True and proof.claims
        provenance = work.snapshot()["provider_calls"]
        message = Message(
            id=uuid.uuid4(),
            project_id=uuid.UUID(project),
            conversation_id=uuid.UUID(conversation),
            role=MessageRole.ASSISTANT,
            content=content,
            grounded=proof.grounded,
            claims=proof.claims,
            citations=[],
            provider=result.provider,
            model=result.model,
            message_metadata={"provider_provenance": provenance},
        )
        MessageRepository(session, uuid.UUID(project)).add(message)
        await session.commit()
        persisted = await session.get(Message, message.id)
        assert persisted.message_metadata["provider_provenance"][0]["reasoning"] == "low"
    response = await db_client.get(path + f"/{conversation}/messages")
    assert response.status_code == 200, response.text
    row = next(row for row in response.json()["data"]["items"] if row["id"] == str(message.id))
    observed = row["provider_provenance"][0]
    assert observed["provider"] == "openai" and observed["model"] == "gpt-6-luna"
    assert observed["purpose"] == "claim_verification" and observed["reasoning"] == "low"
    assert observed["schema_mode"] == ("json_schema" if certified else "prompt")
    assert observed["schema_hash"] and observed["endpoint_hash"] == endpoint_identity(endpoint)
    assert "fixture-not-live" not in response.text
