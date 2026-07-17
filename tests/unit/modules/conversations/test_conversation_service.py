"""Unit tests for ConversationService validation."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import ChatConfig, LLMBackend, LLMConfig
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


async def test_create_rejects_unknown_prompt_version(
    service: ConversationService,
    conversation_repository: AsyncMock,
) -> None:
    with pytest.raises(BadRequestError, match="Unknown system prompt version"):
        await service.create(ConversationCreate(system_prompt_version="v999"))


async def test_create_rejects_unknown_provider(
    service: ConversationService,
) -> None:
    with pytest.raises(BadRequestError, match="Unsupported LLM provider"):
        await service.create(ConversationCreate(provider="not-a-provider"))


async def test_update_rejects_unknown_prompt_version(
    service: ConversationService,
    conversation_repository: AsyncMock,
) -> None:
    conversation_repository.get_by_id = AsyncMock(
        return_value=MagicMock(
            deleted_at=None,
            is_active=True,
            title="x",
            provider="echo",
            model="m",
            temperature=0.5,
            system_prompt_version="v1",
        )
    )
    with pytest.raises(BadRequestError, match="Unknown system prompt version"):
        await service.update(
            uuid.uuid4(),
            ConversationUpdate(system_prompt_version="missing"),
        )


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
