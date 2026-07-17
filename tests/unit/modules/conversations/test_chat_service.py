"""Unit tests for ChatService."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import ChatConfig, LLMBackend, LLMConfig, RetrievalConfig
from app.core.exceptions import ConflictError, NotFoundError, ServiceUnavailableError
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.schemas.message import MessageSendRequest
from app.modules.conversations.services.chat_service import ChatService
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.echo_chat import EchoLLMProvider

pytestmark = pytest.mark.unit


class FakeRetrieval:
    async def retrieve(self, **kwargs: object) -> list[ContextChunk]:
        del kwargs
        return [
            ContextChunk(
                chunk_id=uuid.uuid4(),
                document_id=uuid.uuid4(),
                chunk_index=0,
                content="refund within 30 days",
                score=0.9,
                filename="policy.txt",
                chunk_hash="hash1",
            )
        ]


class FailingLLM(EchoLLMProvider):
    async def generate(self, messages, *, temperature, max_tokens):
        del messages, temperature, max_tokens
        raise ProviderError("boom", provider_name="echo")


class AltModelLLM(EchoLLMProvider):
    def __init__(self) -> None:
        super().__init__(model="alt-model", provider_version="1")


@pytest.fixture
def session() -> AsyncMock:
    mock = AsyncMock()
    mock.in_transaction = MagicMock(return_value=True)

    async def refresh_side_effect(entity: object) -> None:
        if getattr(entity, "id", None) is None:
            entity.id = uuid.uuid4()  # type: ignore[attr-defined]
        now = datetime.now(UTC)
        if getattr(entity, "created_at", None) is None:
            entity.created_at = now  # type: ignore[attr-defined]
        if getattr(entity, "updated_at", None) is None:
            entity.updated_at = now  # type: ignore[attr-defined]
        if getattr(entity, "message_metadata", None) is None:
            entity.message_metadata = {}  # type: ignore[attr-defined]
        if getattr(entity, "citations", None) is None:
            entity.citations = []  # type: ignore[attr-defined]

    mock.commit = AsyncMock()
    mock.rollback = AsyncMock()
    mock.refresh = AsyncMock(side_effect=refresh_side_effect)
    return mock


@pytest.fixture
def conversation() -> Conversation:
    return Conversation(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        title=None,
        provider="echo",
        model="test",
        temperature=0.5,
        system_prompt_version="v1",
        is_active=True,
        deleted_at=None,
        deleted_by=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def conversation_repository(conversation: Conversation) -> AsyncMock:
    mock = AsyncMock()
    mock.get_by_id = AsyncMock(return_value=conversation)
    mock.flush = AsyncMock()
    return mock


@pytest.fixture
def message_repository() -> AsyncMock:
    mock = AsyncMock()
    mock.add = MagicMock(side_effect=lambda entity: entity)
    mock.flush = AsyncMock()
    mock.list_recent_for_conversation = AsyncMock(return_value=[])
    return mock


def _service(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    llm: EchoLLMProvider,
    *,
    chat_config: ChatConfig | None = None,
) -> ChatService:
    return ChatService(
        session=session,
        project_id=uuid.uuid4(),
        conversation_repository=conversation_repository,
        message_repository=message_repository,
        retrieval=FakeRetrieval(),
        chat_config=chat_config or ChatConfig(system_prompt_version="v1"),
        retrieval_config=RetrievalConfig(),
        llm_config=LLMConfig(backend=LLMBackend.ECHO, max_tokens=100, temperature=0.2),
        resolve_llm=lambda _conversation: llm,
    )


async def test_zero_history_limit_excludes_prior_messages(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    captured_contents: list[str] = []

    class CapturingLLM(EchoLLMProvider):
        async def generate(self, messages, *, temperature, max_tokens):
            captured_contents.extend(message.content for message in messages)
            return await super().generate(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    prior = Message(
        id=uuid.uuid4(),
        project_id=conversation.project_id,
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="must not be included",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    message_repository.list_recent_for_conversation.return_value = [prior]
    service = _service(
        session,
        conversation_repository,
        message_repository,
        CapturingLLM(model="test", provider_version="1"),
        chat_config=ChatConfig(system_prompt_version="v1", max_history_messages=0),
    )

    await service.send_message(
        conversation.id,
        MessageSendRequest(content="current question"),
    )

    assert "must not be included" not in captured_contents
    assert captured_contents[-1] == "current question"


async def test_send_message_commits_user_before_assistant(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
) -> None:
    service = _service(
        session,
        conversation_repository,
        message_repository,
        EchoLLMProvider(model="test", provider_version="1"),
    )
    turn = await service.send_message(
        conversation_repository.get_by_id.return_value.id,
        MessageSendRequest(content="What is the refund policy?"),
    )
    assert session.commit.await_count == 2
    assert session.rollback.await_count >= 1
    assert turn.user_message.content == "What is the refund policy?"
    assert turn.assistant_message.content.startswith("[echo]")
    assert turn.assistant_message.citations
    assert conversation_repository.get_by_id.return_value.title is not None


async def test_send_message_llm_failure_leaves_user_only(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
) -> None:
    service = _service(
        session,
        conversation_repository,
        message_repository,
        FailingLLM(model="test", provider_version="1"),
    )
    with pytest.raises(ServiceUnavailableError, match="temporarily unavailable"):
        await service.send_message(
            conversation_repository.get_by_id.return_value.id,
            MessageSendRequest(content="question"),
        )
    assert session.commit.await_count == 1


async def test_send_message_uses_conversation_temperature(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    captured: dict[str, float] = {}

    class CapturingLLM(EchoLLMProvider):
        async def generate(self, messages, *, temperature, max_tokens):
            captured["temperature"] = temperature
            return await super().generate(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    conversation.temperature = 0.5
    service = _service(
        session,
        conversation_repository,
        message_repository,
        CapturingLLM(model="test", provider_version="1"),
    )
    await service.send_message(
        conversation.id,
        MessageSendRequest(content="hello"),
    )
    assert captured["temperature"] == 0.5


async def test_resolve_llm_uses_conversation_model(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    conversation.model = "alt-model"
    service = _service(
        session,
        conversation_repository,
        message_repository,
        AltModelLLM(),
    )
    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="hello"),
    )
    assert turn.assistant_message.model == "alt-model"


async def test_releases_read_transaction_before_generation(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
) -> None:
    service = _service(
        session,
        conversation_repository,
        message_repository,
        EchoLLMProvider(model="test", provider_version="1"),
    )
    await service.send_message(
        conversation_repository.get_by_id.return_value.id,
        MessageSendRequest(content="hello"),
    )
    session.rollback.assert_awaited()
    assert session.commit.await_count == 2


async def test_deleted_conversation_rejected(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    conversation.deleted_at = datetime.now(UTC)
    service = _service(
        session,
        conversation_repository,
        message_repository,
        EchoLLMProvider(model="test", provider_version="1"),
    )
    with pytest.raises(ConflictError, match="Cannot modify a deleted conversation"):
        await service.send_message(
            conversation.id,
            MessageSendRequest(content="hello"),
        )


async def test_inactive_conversation_rejected(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    conversation.is_active = False
    service = _service(
        session,
        conversation_repository,
        message_repository,
        EchoLLMProvider(model="test", provider_version="1"),
    )
    with pytest.raises(NotFoundError, match="not active"):
        await service.send_message(
            conversation.id,
            MessageSendRequest(content="hello"),
        )


async def test_stream_message_yields_done_event(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    service = _service(
        session,
        conversation_repository,
        message_repository,
        EchoLLMProvider(model="test", provider_version="1"),
    )
    events: list[str | dict] = []
    async for item in service.stream_message(
        conversation.id,
        MessageSendRequest(content="stream me"),
    ):
        events.append(item)
    assert any(isinstance(item, str) for item in events)
    done = next(item for item in events if isinstance(item, dict))
    assert done["event"] == "done"
    assert done["assistant_message_id"]


async def test_stream_cancel_skips_assistant_persist(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    service = _service(
        session,
        conversation_repository,
        message_repository,
        EchoLLMProvider(model="test", provider_version="1"),
    )
    cancel_after_first = False

    async def should_cancel() -> bool:
        return cancel_after_first

    events: list[str | dict] = []
    async for item in service.stream_message(
        conversation.id,
        MessageSendRequest(content="one two three"),
        should_cancel=should_cancel,
    ):
        if isinstance(item, str):
            cancel_after_first = True
        events.append(item)
    assert session.commit.await_count == 1
    assert not any(isinstance(item, dict) for item in events)
