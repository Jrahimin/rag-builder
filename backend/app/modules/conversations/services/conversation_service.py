"""Conversation business orchestration and transaction boundaries."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ChatConfig, LLMBackend, LLMConfig
from app.core.exceptions import BadRequestError
from app.models.conversation import Conversation
from app.modules.conversations.prompts.registry import require_prompt_template
from app.modules.conversations.repositories.conversation_repository import ConversationRepository
from app.modules.conversations.repositories.message_repository import MessageRepository
from app.modules.conversations.schemas.conversation import ConversationCreate, ConversationUpdate
from app.platform.domain.lifecycle_service import (
    get_or_raise,
    list_paginated,
    require_not_deleted,
    toggle_active_status,
)
from app.platform.domain.lifecycle_service import (
    soft_delete as soft_delete_entity,
)
from app.platform.domain.transactions import flush_commit_refresh
from app.platform.http.pagination import ListParams, PaginatedResult

_NOT_FOUND = {"message": "Conversation not found.", "code": "conversation_not_found"}
_DELETED = {"message": "Cannot modify a deleted conversation.", "code": "conversation_deleted"}


def _validate_provider(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        LLMBackend(value)
    except ValueError:
        raise BadRequestError(
            message=f"Unsupported LLM provider: {value}",
            code="unsupported_llm_provider",
        ) from None
    return value


def _validate_prompt_version(value: str | None, *, default: str) -> str:
    version = value or default
    require_prompt_template(version)
    return version


class ConversationService:
    """Orchestrates conversation CRUD and message listing."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        conversation_repository: ConversationRepository,
        message_repository: MessageRepository,
        *,
        llm_config: LLMConfig,
        chat_config: ChatConfig,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._conversation_repository = conversation_repository
        self._message_repository = message_repository
        self._llm_config = llm_config
        self._chat_config = chat_config

    async def create(self, data: ConversationCreate) -> Conversation:
        provider = _validate_provider(data.provider) or self._llm_config.backend.value
        prompt_version = _validate_prompt_version(
            data.system_prompt_version,
            default=self._chat_config.system_prompt_version,
        )
        conversation = Conversation(
            project_id=self._project_id,
            title=data.title,
            provider=provider,
            model=data.model or self._llm_config.model,
            temperature=(
                data.temperature if data.temperature is not None else self._llm_config.temperature
            ),
            system_prompt_version=prompt_version,
            is_active=True,
        )
        self._conversation_repository.add(conversation)
        return await flush_commit_refresh(
            self._session,
            self._conversation_repository,
            conversation,
        )

    async def get(self, conversation_id: uuid.UUID) -> Conversation:
        return await get_or_raise(
            self._conversation_repository,
            conversation_id,
            message=_NOT_FOUND["message"],
            code=_NOT_FOUND["code"],
        )

    async def list(self, params: ListParams) -> PaginatedResult[Conversation]:
        return await list_paginated(self._conversation_repository, params)

    async def update(self, conversation_id: uuid.UUID, data: ConversationUpdate) -> Conversation:
        if not data.model_fields_set:
            raise BadRequestError(
                message="At least one field must be provided.",
                code="empty_update",
            )

        conversation = await self._require_mutable(conversation_id)

        if "title" in data.model_fields_set:
            conversation.title = data.title
        if "provider" in data.model_fields_set:
            conversation.provider = _validate_provider(data.provider)
        if "model" in data.model_fields_set:
            conversation.model = data.model
        if "temperature" in data.model_fields_set:
            conversation.temperature = data.temperature
        if "system_prompt_version" in data.model_fields_set:
            conversation.system_prompt_version = _validate_prompt_version(
                data.system_prompt_version,
                default=self._chat_config.system_prompt_version,
            )

        return await flush_commit_refresh(
            self._session,
            self._conversation_repository,
            conversation,
        )

    async def toggle_status(self, conversation_id: uuid.UUID) -> Conversation:
        return await toggle_active_status(
            self._session,
            self._conversation_repository,
            conversation_id,
            not_found_message=_NOT_FOUND["message"],
            not_found_code=_NOT_FOUND["code"],
            deleted_message=_DELETED["message"],
            deleted_code=_DELETED["code"],
        )

    async def soft_delete(self, conversation_id: uuid.UUID) -> Conversation:
        return await soft_delete_entity(
            self._session,
            self._conversation_repository,
            conversation_id,
            not_found_message=_NOT_FOUND["message"],
            not_found_code=_NOT_FOUND["code"],
        )

    async def list_messages(
        self,
        conversation_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
    ) -> PaginatedResult:
        items = await self._message_repository.list_by_conversation(
            conversation_id,
            limit=limit,
            offset=offset,
        )
        total = await self._message_repository.count_by_conversation(conversation_id)
        return PaginatedResult(items=items, total=total, limit=limit, offset=offset)

    async def _require_mutable(self, conversation_id: uuid.UUID) -> Conversation:
        conversation = await get_or_raise(
            self._conversation_repository,
            conversation_id,
            message=_NOT_FOUND["message"],
            code=_NOT_FOUND["code"],
            include_deleted=True,
        )
        require_not_deleted(conversation, **_DELETED)
        return conversation
