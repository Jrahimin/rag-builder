"""Unit tests for ConversationService validation."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.core.config import ChatConfig, LLMBackend, LLMConfig, Settings
from app.core.exceptions import BadRequestError
from app.models.conversation import Conversation
from app.modules.conversations.schemas.conversation import ConversationCreate, ConversationUpdate
from app.modules.conversations.services.conversation_service import ConversationService

pytestmark = pytest.mark.unit


@pytest.fixture
def session() -> AsyncMock:
    mock = AsyncMock()
    mock.commit = AsyncMock()
    mock.refresh = AsyncMock()
    return mock


@pytest.fixture
def conversation_repository() -> AsyncMock:
    mock = AsyncMock()
    mock.add = MagicMock(side_effect=lambda entity: entity)
    mock.flush = AsyncMock()
    return mock


@pytest.fixture
def message_repository() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def service(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
) -> ConversationService:
    return ConversationService(
        session=session,
        project_id=uuid.uuid4(),
        conversation_repository=conversation_repository,
        message_repository=message_repository,
        llm_config=LLMConfig(backend=LLMBackend.ECHO, model="test-model"),
        chat_config=ChatConfig(),
    )


async def test_create_rejects_prompt_version_request_field(
    service: ConversationService,
    conversation_repository: AsyncMock,
) -> None:
    del service, conversation_repository
    with pytest.raises(ValidationError):
        ConversationCreate(system_prompt_version="v999")  # type: ignore[call-arg]


async def test_create_rejects_unknown_provider(
    service: ConversationService,
) -> None:
    with pytest.raises(BadRequestError, match="Unsupported LLM provider"):
        await service.create(ConversationCreate(provider="not-a-provider"))


async def test_update_rejects_prompt_version_request_field(
    service: ConversationService,
    conversation_repository: AsyncMock,
) -> None:
    del service, conversation_repository
    with pytest.raises(ValidationError):
        ConversationUpdate(system_prompt_version="missing")  # type: ignore[call-arg]


async def test_create_persists_conversation(
    service: ConversationService,
    session: AsyncMock,
    conversation_repository: AsyncMock,
) -> None:
    result = await service.create(ConversationCreate())

    assert result.is_active is True
    assert result.provider == "echo"
    assert result.model == "test-model"
    conversation_repository.add.assert_called_once()
    session.commit.assert_awaited_once()


async def test_new_conversations_capture_new_profile_without_mutating_old_snapshot(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
) -> None:
    session.scalar.return_value = 0

    def assign_conversation_id(conversation: Conversation) -> Conversation:
        conversation.id = uuid.uuid4()
        return conversation

    conversation_repository.add.side_effect = assign_conversation_id
    project_id = uuid.uuid4()
    common = {
        "session": session,
        "project_id": project_id,
        "conversation_repository": conversation_repository,
        "message_repository": message_repository,
        "llm_config": LLMConfig(backend=LLMBackend.ECHO, model="test-model"),
        "chat_config": ChatConfig(),
    }
    standard = ConversationService(
        **common,
        settings=Settings(ai_policy={"default_rag_profile": "standard"}),
    )
    quality = ConversationService(
        **common,
        settings=Settings(ai_policy={"default_rag_profile": "quality"}),
    )

    await standard.create(ConversationCreate())
    first_snapshot = session.add.await_args_list[-1].args[0]
    await quality.create(ConversationCreate())
    second_snapshot = session.add.await_args_list[-1].args[0]

    assert first_snapshot.provenance["execution_profile_id"] == "standard"
    assert first_snapshot.configuration["retrieval"]["top_k"] == 10
    assert second_snapshot.provenance["execution_profile_id"] == "quality"
    assert second_snapshot.configuration["retrieval"]["top_k"] == 12
    assert first_snapshot.configuration["retrieval"]["top_k"] == 10


async def test_soft_delete_does_not_touch_messages(
    service: ConversationService,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
) -> None:
    conversation_id = uuid.uuid4()
    deleted = Conversation(
        id=conversation_id,
        project_id=service._project_id,
        title="t",
        provider="echo",
        model="m",
        temperature=0.5,
        system_prompt_version="v1",
        is_active=True,
        deleted_at=None,
        deleted_by=None,
    )
    conversation_repository.get_by_id = AsyncMock(return_value=deleted)
    conversation_repository.flush = AsyncMock()

    result = await service.soft_delete(conversation_id)

    assert result.deleted_at is not None
    message_repository.delete.assert_not_called()
    message_repository.list_by_conversation.assert_not_called()
