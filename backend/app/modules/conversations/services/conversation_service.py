"""Conversation business orchestration and transaction boundaries."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ChatConfig, LLMBackend, LLMConfig, Settings
from app.core.exceptions import BadRequestError, ConflictError
from app.models.conversation import Conversation
from app.models.conversation_config_snapshot import ConversationConfigSnapshot
from app.modules.conversations.prompts.registry import (
    GROUNDED_PROMPT_VERSION,
    require_prompt_template,
)
from app.modules.conversations.repositories.config_snapshot_repository import (
    ConversationConfigSnapshotRepository,
)
from app.modules.conversations.repositories.conversation_repository import ConversationRepository
from app.modules.conversations.repositories.message_repository import MessageRepository
from app.modules.conversations.schemas.conversation import ConversationCreate, ConversationUpdate
from app.platform.audit.contracts import (
    AuditActorType,
    AuditEventType,
    AuditOutcome,
    AuditRecorder,
)
from app.platform.config.project_ai import (
    ConfigRevisionRecord,
    EffectiveConfigResolution,
    resolve_project_ai_config,
)
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


def _canonical_prompt_version() -> str:
    require_prompt_template(GROUNDED_PROMPT_VERSION)
    return GROUNDED_PROMPT_VERSION


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
        settings: Settings | None = None,
        active_revision: ConfigRevisionRecord | None = None,
        actor_id: str = "external",
        audit: AuditRecorder | None = None,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._conversation_repository = conversation_repository
        self._message_repository = message_repository
        self._llm_config = llm_config
        self._chat_config = chat_config
        self._settings = settings or Settings(llm=llm_config, chat=chat_config)
        self._active_revision = active_revision
        self._actor_id = actor_id
        self._audit = audit
        self._snapshots = ConversationConfigSnapshotRepository(session, project_id)

    async def create(self, data: ConversationCreate) -> Conversation:
        provider_value = getattr(data, "provider", None)
        if provider_value is not None:
            _validate_provider(provider_value)
        config_fields = {
            "provider",
            "model",
            "temperature",
        } & data.model_fields_set
        # system_prompt_version removed from request surface in Phase 3.
        resolution = self._resolve({field: getattr(data, field, None) for field in config_fields})
        prompt_version = _canonical_prompt_version()
        conversation = Conversation(
            project_id=self._project_id,
            title=data.title,
            provider=resolution.configuration.llm.provider.value,
            model=resolution.configuration.llm.model,
            temperature=resolution.configuration.llm.temperature,
            system_prompt_version=prompt_version,
            is_active=True,
        )
        self._conversation_repository.add(conversation)
        await self._conversation_repository.flush()
        snapshot = await self._append_snapshot(
            conversation,
            resolution,
            reason="Conversation creation",
        )
        conversation.active_config_snapshot_id = snapshot.id
        await self._session.commit()
        await self._session.refresh(conversation)
        return conversation

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
        config_fields = {
            "provider",
            "model",
            "temperature",
        } & data.model_fields_set
        # system_prompt_version removed from request surface in Phase 3.
        if config_fields:
            provider_value = getattr(data, "provider", None)
            if provider_value is not None:
                _validate_provider(provider_value)
            resolution = self._resolve(
                {field: getattr(data, field, None) for field in config_fields}
            )
            conversation.provider = resolution.configuration.llm.provider.value
            conversation.model = resolution.configuration.llm.model
            conversation.temperature = resolution.configuration.llm.temperature
            conversation.system_prompt_version = _canonical_prompt_version()
            snapshot = await self._append_snapshot(
                conversation,
                resolution,
                reason="Deprecated request compatibility update",
            )
            conversation.active_config_snapshot_id = snapshot.id

        return await flush_commit_refresh(
            self._session,
            self._conversation_repository,
            conversation,
        )

    async def refresh_config(
        self,
        conversation_id: uuid.UUID,
        *,
        expected_active_config_snapshot_id: uuid.UUID | None,
        reason: str,
    ) -> Conversation:
        conversation = await self._require_mutable(conversation_id)
        if conversation.active_config_snapshot_id != expected_active_config_snapshot_id:
            raise ConflictError(
                message="The active conversation configuration changed.",
                code="conversation_config_snapshot_conflict",
            )
        resolution = self._resolve({})
        snapshot = await self._append_snapshot(
            conversation,
            resolution,
            reason=reason,
        )
        conversation.active_config_snapshot_id = snapshot.id
        conversation.provider = resolution.configuration.llm.provider.value
        conversation.model = resolution.configuration.llm.model
        conversation.temperature = resolution.configuration.llm.temperature
        conversation.system_prompt_version = GROUNDED_PROMPT_VERSION
        if self._audit is not None:
            self._audit.record(
                event_type=AuditEventType.CONVERSATION_CONFIG_UPDATED,
                actor_type=AuditActorType.OPERATOR,
                actor_id=self._actor_id,
                project_id=self._project_id,
                resource_type="conversation_config_snapshot",
                resource_id=snapshot.id,
                outcome=AuditOutcome.SUCCESS,
                detail={
                    "conversation_id": str(conversation.id),
                    "configuration_hash": snapshot.configuration_hash,
                    "reason": reason.strip(),
                },
            )
        await self._session.commit()
        await self._session.refresh(conversation)
        return conversation

    def _resolve(self, deprecated_overrides: dict[str, object]) -> EffectiveConfigResolution:
        return resolve_project_ai_config(
            self._settings,
            self._active_revision,
            deprecated_overrides=deprecated_overrides,
        )

    async def _append_snapshot(
        self,
        conversation: Conversation,
        resolution: EffectiveConfigResolution,
        *,
        reason: str,
    ) -> ConversationConfigSnapshot:
        snapshot = ConversationConfigSnapshot(
            project_id=self._project_id,
            conversation_id=conversation.id,
            sequence=await self._snapshots.next_sequence(conversation.id),
            schema_version=4,
            configuration_hash=resolution.configuration_hash,
            resolution_fingerprint=resolution.resolution_fingerprint,
            configuration=resolution.configuration.model_dump(mode="json"),
            provenance=resolution.provenance.model_dump(mode="json"),
            origins=resolution.origins,
            structured_origins={
                path: origin.model_dump(mode="json")
                for path, origin in resolution.structured_origins.items()
            },
            invariants=resolution.invariants.model_dump(mode="json"),
            compatibility_diagnostics=resolution.compatibility_diagnostics,
            created_by=self._actor_id,
            reason=reason,
        )
        await self._snapshots.add(snapshot)
        await self._session.flush()
        return snapshot

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
