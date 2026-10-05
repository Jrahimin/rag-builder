"""Stored amendment membership closes a scoped production turn without an absence claim."""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.composition.source_metadata import KnowledgeRetrievalSourceMetadataAdapter
from app.core.config import ChatConfig, LLMConfig
from app.dependencies.conversations import SearchServiceRetrievalAdapter
from app.dependencies.retrieval import get_search_service
from app.modules.conversations.repositories.conversation_repository import ConversationRepository
from app.modules.conversations.repositories.message_repository import MessageRepository
from app.modules.conversations.schemas.message import MessageResponse, MessageSendRequest
from app.modules.conversations.services.chat_service import ChatService
from app.platform.jobs.contracts import JobDefinition
from app.platform.providers.contracts.llm import ChatCompletionResult, ChatUsage
from app.platform.providers.implementations.echo_chat import EchoLLMProvider
from app.platform.providers.implementations.embedding_factory import get_embedding_provider
from app.platform.providers.request_work import current_request_purpose
from tests.integration.knowledge_helpers import run_captured_document_jobs
from tests.integration.test_phase3_source_retrieval import (
    _index_documents,
    _project_id,
    _revision,
    _upload,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class MissingAmendmentPlan(EchoLLMProvider):
    """Only a local JSON protocol fixture; retrieval decisions come from storage."""

    def __init__(self):
        super().__init__(model="test", provider_version="fixture")
        self.purposes = []

    async def generate(self, messages, *, temperature=None, max_tokens, output_contract=None):
        del messages, temperature, max_tokens, output_contract
        purpose = current_request_purpose()
        self.purposes.append(purpose)
        assert purpose in {"recovery_planning", "structured_response_retry"}
        requirement = {
            "requirement_id": "R1",
            "description": "Applicable governing amendment for Section 21",
            "origin": "explicit_user_request",
            "materiality": "governing_applicability",
        }
        content = {
            "requirements": [requirement],
            "queries": [{"query": "Section 21 amendment", "requirement_ids": ["R1"]}],
            "coverage": {
                "complete": False,
                "missing": [requirement["description"]],
                "checks": [
                    {
                        "requirement_id": "R1",
                        "description": requirement["description"],
                        "supported": False,
                        "fulfillment": "none",
                        "evidence": [],
                    }
                ],
            },
        }
        return ChatCompletionResult(
            content=json.dumps(content),
            provider="echo",
            model="test",
            provider_version="fixture",
            finish_reason="stop",
            usage=ChatUsage(1, 1),
        )


async def test_persisted_scoped_known_gap_uses_real_metadata_search_adapter_and_message_get(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
):
    project = await _project_id(db_client)
    base = await _upload(
        db_client, project, "base-rule.txt", "Section 21 investment rebate rate is 10 percent"
    )
    base_revision = await _revision(
        db_client,
        project,
        base,
        {
            "title": "Base investment rule",
            "revision_label": "Base 2025",
            "effective_from": "2025-01-01",
            "source_role": "primary",
        },
    )
    await _index_documents(db_client, integration_connection, captured_jobs, project, [base])
    # The immutable build predates this document; processing it does not add
    # membership. This is a stored MODIFIES dependency, never a search-miss signal.
    amendment = await _upload(db_client, project, "amendment.txt", "Section 21 rate is amended")
    revision = await _revision(
        db_client,
        project,
        amendment,
        {
            "title": "Governing amendment",
            "revision_label": "Amendment 2026",
            "effective_from": "2026-01-01",
            "source_role": "supporting",
            "relationships": [
                {
                    "relationship_type": "modifies",
                    "target_revision_id": base_revision["id"],
                    "target_provisions": ["Section 21"],
                }
            ],
        },
    )
    await run_captured_document_jobs(integration_connection, captured_jobs)
    generation = (await db_client.get(f"/api/v1/projects/{project}/sources")).json()["data"][
        "generation"
    ]
    created = await db_client.post(f"/api/v1/projects/{project}/conversations", json={})
    assert created.status_code == 201, created.text
    conversation_id = uuid.UUID(created.json()["data"]["id"])
    identity = uuid.UUID(project)
    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        search = await get_search_service(session, identity, get_embedding_provider())
        adapter = SearchServiceRetrievalAdapter(search)
        found = await adapter.retrieve(
            query="Section 21 investment rebate rate", top_k=5, document_id=uuid.UUID(base)
        )
        diagnostics = found.diagnostics
        assert diagnostics["source_policy_effective_mode"] == "enforce"
        assert diagnostics["known_corpus_gap"] is True
        binding = diagnostics["known_corpus_gap_binding"]
        assert binding["project_id"] == project
        assert binding["source_metadata_generation"] == generation
        assert binding["producer"] == "source_metadata_dependency.v1"
        assert any(
            row["modifier_revision_id"] == revision["id"]
            and row["outcome"] == "not_in_active_index"
            for row in diagnostics["modifies_expansion_records"]
        )
        # The real SQL reader must not carry this dependency across projects
        # or backwards into a generation that predates the stored amendment.
        reader = KnowledgeRetrievalSourceMetadataAdapter(session)
        for other_project, old_generation in [(uuid.uuid4(), generation), (identity, 1)]:
            records = await reader.incoming_modifiers(
                project_id=other_project,
                base_revision_ids=(uuid.UUID(base_revision["id"]),),
                generation=old_generation,
                as_of=None,
                index_build_id=uuid.UUID(binding["index_build_id"]),
            )
            assert all(row.outcome.value != "not_in_active_index" for row in records)
        await session.rollback()
        provider = MissingAmendmentPlan()
        service = ChatService(
            session,
            identity,
            ConversationRepository(session, identity),
            MessageRepository(session, identity),
            adapter,
            ChatConfig(execution_policy="adaptive_v1", bounded_recovery_enabled=True),
            search._config,
            LLMConfig(model="test"),
            resolve_llm=lambda _: provider,
        )
        response = (
            await service.send_message(
                conversation_id,
                MessageSendRequest(
                    content="What current investment rebate rate applies under Section 21?",
                    document_id=uuid.UUID(base),
                ),
            )
        ).assistant_message
        assert response.terminal_outcome.outcome == "insufficient_evidence"
        assert response.terminal_outcome.retryable is False
        assert response.grounded is False and response.claims == [] and response.citations == []
        assert "Section 21" in response.content
        assert "never enacted" not in response.content
        assert response.metadata["knowledge_repair"]["stop_reason"] == "known_corpus_gap"
        deadline = response.metadata["lifecycle"]["deadline"]
        assert deadline["budget_class"] == "simple"
        assert deadline["promoted"] is False
        assert "answer_generation" not in provider.purposes
        assert response.metadata["lifecycle"]["persistence_completed"] is True
        saved = await MessageRepository(session, identity).get_by_id(response.id)
        assert saved is not None
        projection = MessageResponse.from_message(saved)
        assert projection.terminal_outcome == response.terminal_outcome
        assert projection.claims == response.claims and projection.notices == response.notices
        assert projection.metadata["execution"] == response.metadata["execution"]
    fetched = await db_client.get(
        f"/api/v1/projects/{project}/conversations/{conversation_id}/messages"
    )
    assert fetched.status_code == 200, fetched.text
    rows = fetched.json()["data"]["items"]
    assistant = next(row for row in rows if row["id"] == str(response.id))
    assert assistant["terminal_outcome"] == response.terminal_outcome.model_dump(mode="json")
    assert assistant["claims"] == [] and assistant["grounded"] is False
