"""RAG chat orchestration with split transaction boundaries."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ChatConfig, LLMConfig, RetrievalConfig
from app.core.exceptions import NotFoundError, ServiceUnavailableError
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.modules.conversations.citation_snapshots import build_citation_snapshots
from app.modules.conversations.context_builder import ContextBuilder
from app.modules.conversations.ports import ContextChunk, RetrievalPort
from app.modules.conversations.prompt_builder import PromptBuilder
from app.modules.conversations.prompts.registry import PromptTemplate, require_prompt_template
from app.modules.conversations.repositories.conversation_repository import ConversationRepository
from app.modules.conversations.repositories.message_repository import MessageRepository
from app.modules.conversations.schemas.message import (
    ChatTurnResponse,
    MessageResponse,
    MessageSendRequest,
)
from app.platform.domain.lifecycle_service import get_or_raise, require_not_deleted
from app.platform.domain.transactions import commit_refresh
from app.platform.providers.contracts.llm import BaseLLMProvider, ChatMessage
from app.platform.providers.errors import ProviderError

logger = structlog.get_logger(__name__)

type ShouldCancelFn = Callable[[], Awaitable[bool]]
type LLMProviderResolver = Callable[[Conversation], BaseLLMProvider]

_NOT_FOUND = {"message": "Conversation not found.", "code": "conversation_not_found"}
_DELETED = {"message": "Cannot modify a deleted conversation.", "code": "conversation_deleted"}


@dataclass(frozen=True, slots=True)
class _PreparedTurn:
    """Retrieved context and prompt messages ready for generation."""

    prompt_version: str
    template: PromptTemplate
    selected: list[ContextChunk]
    chunks: list[ContextChunk]
    history: list[Message]
    messages: list[ChatMessage]
    temperature: float
    llm: BaseLLMProvider
    retrieval_ms: int


class ChatService:
    """Orchestrates retrieve → prompt → LLM → persist with Tx1/Tx2 commits."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        conversation_repository: ConversationRepository,
        message_repository: MessageRepository,
        retrieval: RetrievalPort,
        chat_config: ChatConfig,
        retrieval_config: RetrievalConfig,
        llm_config: LLMConfig,
        *,
        resolve_llm: LLMProviderResolver,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._conversation_repository = conversation_repository
        self._message_repository = message_repository
        self._retrieval = retrieval
        self._chat_config = chat_config
        self._retrieval_config = retrieval_config
        self._llm_config = llm_config
        self._resolve_llm = resolve_llm
        self._context_builder = ContextBuilder(chat_config)
        self._prompt_builder = PromptBuilder()

    async def send_message(
        self,
        conversation_id: uuid.UUID,
        request: MessageSendRequest,
    ) -> ChatTurnResponse:
        conversation = await self._require_mutable_conversation(conversation_id)
        started = time.perf_counter()

        user_message = await self._commit_user_message(conversation, request.content)
        conversation_provider = conversation.provider
        conversation_model = conversation.model
        user_message_response = self._to_response(
            user_message,
            conversation_provider=conversation_provider,
            conversation_model=conversation_model,
        )
        prepared = await self._prepare_turn(
            conversation=conversation,
            conversation_id=conversation_id,
            user_message=user_message,
            request=request,
        )

        generation_started = time.perf_counter()
        try:
            completion = await prepared.llm.generate(
                prepared.messages,
                temperature=prepared.temperature,
                max_tokens=self._llm_max_tokens(),
            )
        except ProviderError as exc:
            self._log_provider_failure(conversation_id, exc)
            raise self._provider_unavailable(exc) from exc

        generation_ms = int((time.perf_counter() - generation_started) * 1000)
        total_ms = int((time.perf_counter() - started) * 1000)

        assistant_message = await self._persist_assistant_turn(
            conversation=conversation,
            prepared=prepared,
            content=completion.content,
            finish_reason=completion.finish_reason,
            input_tokens=completion.usage.input_tokens,
            output_tokens=completion.usage.output_tokens,
            provider=completion.provider,
            model=completion.model,
            generation_ms=generation_ms,
            total_ms=total_ms,
            user_content_for_title=request.content,
            streamed=False,
            input_tokens_logged=completion.usage.input_tokens,
            output_tokens_logged=completion.usage.output_tokens,
        )

        return ChatTurnResponse(
            user_message=user_message_response,
            assistant_message=self._to_response(
                assistant_message,
                conversation_provider=conversation_provider,
                conversation_model=conversation_model,
            ),
        )

    async def stream_message(
        self,
        conversation_id: uuid.UUID,
        request: MessageSendRequest,
        *,
        should_cancel: ShouldCancelFn | None = None,
    ) -> AsyncIterator[str | dict[str, Any]]:
        """Yield SSE payload fragments: token strings, then final citations dict."""
        conversation = await self._require_mutable_conversation(conversation_id)
        started = time.perf_counter()

        user_message = await self._commit_user_message(conversation, request.content)
        prepared = await self._prepare_turn(
            conversation=conversation,
            conversation_id=conversation_id,
            user_message=user_message,
            request=request,
        )

        if should_cancel is not None and await should_cancel():
            return

        generation_started = time.perf_counter()
        content_parts: list[str] = []
        finish_reason: str | None = None

        try:
            async for chunk in prepared.llm.stream(
                prepared.messages,
                temperature=prepared.temperature,
                max_tokens=self._llm_max_tokens(),
            ):
                if should_cancel is not None and await should_cancel():
                    break
                if chunk.delta:
                    content_parts.append(chunk.delta)
                    yield chunk.delta
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
        except ProviderError as exc:
            self._log_provider_failure(conversation_id, exc)
            raise self._provider_unavailable(exc) from exc

        if should_cancel is not None and await should_cancel():
            return

        generation_ms = int((time.perf_counter() - generation_started) * 1000)
        total_ms = int((time.perf_counter() - started) * 1000)
        full_content = "".join(content_parts)

        assistant_message = await self._persist_assistant_turn(
            conversation=conversation,
            prepared=prepared,
            content=full_content,
            finish_reason=finish_reason or "stop",
            input_tokens=None,
            output_tokens=None,
            provider=prepared.llm.provider_name,
            model=prepared.llm.model_name,
            generation_ms=generation_ms,
            total_ms=total_ms,
            user_content_for_title=request.content,
            streamed=True,
            input_tokens_logged=None,
            output_tokens_logged=None,
        )

        yield {
            "event": "done",
            "assistant_message_id": str(assistant_message.id),
            "citations": self._citations_for(prepared.selected),
        }

    async def _prepare_turn(
        self,
        *,
        conversation: Conversation,
        conversation_id: uuid.UUID,
        user_message: Message,
        request: MessageSendRequest,
    ) -> _PreparedTurn:
        retrieval_started = time.perf_counter()
        chunks = await self._retrieval.retrieve(
            query=request.content,
            top_k=self._chat_config.retrieval_top_k,
            document_id=request.document_id,
            metadata_filter=request.metadata_filter or None,
        )
        retrieval_ms = int((time.perf_counter() - retrieval_started) * 1000)
        selected = self._context_builder.select(chunks)

        prompt_version = (
            conversation.system_prompt_version or self._chat_config.system_prompt_version
        )
        template = require_prompt_template(prompt_version)
        history_limit = self._chat_config.max_history_messages
        fetch_limit = history_limit + 1 if history_limit > 0 else 1
        history = await self._message_repository.list_recent_for_conversation(
            conversation_id,
            limit=fetch_limit,
        )
        history = [message for message in history if message.id != user_message.id]
        history = history[-history_limit:] if history_limit > 0 else []

        messages = self._prompt_builder.build(
            template=template,
            context_chunks=selected,
            history=history,
            user_question=request.content,
        )
        llm = self._resolve_llm(conversation)
        temperature = self._effective_temperature(conversation)

        await self._release_read_transaction()

        return _PreparedTurn(
            prompt_version=prompt_version,
            template=template,
            selected=selected,
            chunks=chunks,
            history=history,
            messages=messages,
            temperature=temperature,
            llm=llm,
            retrieval_ms=retrieval_ms,
        )

    async def _persist_assistant_turn(
        self,
        *,
        conversation: Conversation,
        prepared: _PreparedTurn,
        content: str,
        finish_reason: str | None,
        input_tokens: int | None,
        output_tokens: int | None,
        provider: str,
        model: str,
        generation_ms: int,
        total_ms: int,
        user_content_for_title: str,
        streamed: bool,
        input_tokens_logged: int | None,
        output_tokens_logged: int | None,
    ) -> Message:
        metadata = self._build_metadata(
            retrieval_ms=prepared.retrieval_ms,
            generation_ms=generation_ms,
            total_ms=total_ms,
            retrieved_count=len(prepared.chunks),
            selected_count=len(prepared.selected),
        )
        citations = self._citations_for(prepared.selected)
        assistant_message = await self._commit_assistant_message(
            conversation=conversation,
            content=content,
            finish_reason=finish_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prompt_version=prepared.prompt_version,
            provider=provider,
            model=model,
            metadata=metadata,
            citations=citations,
            user_content_for_title=user_content_for_title,
        )
        log_kwargs: dict[str, Any] = {
            "project_id": str(self._project_id),
            "conversation_id": str(conversation.id),
            "total_time_ms": total_ms,
            "retrieval_time_ms": prepared.retrieval_ms,
            "generation_time_ms": generation_ms,
            "retrieval_strategy": self._retrieval_config.strategy.value,
            "retrieval_top_k": self._chat_config.retrieval_top_k,
            "retrieved_chunk_count": len(prepared.chunks),
            "provider": provider,
            "model": model,
            "streamed": streamed,
        }
        if input_tokens_logged is not None:
            log_kwargs["input_tokens"] = input_tokens_logged
        if output_tokens_logged is not None:
            log_kwargs["output_tokens"] = output_tokens_logged
        logger.info("chat_complete", **log_kwargs)
        return assistant_message

    def _citations_for(self, selected: list[ContextChunk]) -> list[dict]:
        if not self._chat_config.include_citations:
            return []
        return build_citation_snapshots(selected, config=self._chat_config)

    async def _release_read_transaction(self) -> None:
        """Close any implicit read transaction before slow external I/O."""
        await self._session.rollback()

    def _effective_temperature(self, conversation: Conversation) -> float:
        if conversation.temperature is not None:
            return conversation.temperature
        return self._llm_config.temperature

    def _provider_unavailable(self, exc: ProviderError) -> ServiceUnavailableError:
        return ServiceUnavailableError(
            message="The language model provider is temporarily unavailable.",
            code="llm_provider_unavailable",
            context={"provider": exc.provider_name, "error": str(exc)},
        )

    def _log_provider_failure(self, conversation_id: uuid.UUID, exc: ProviderError) -> None:
        logger.warning(
            "chat_failed",
            project_id=str(self._project_id),
            conversation_id=str(conversation_id),
            provider=exc.provider_name,
            error=str(exc),
        )

    async def _commit_user_message(
        self,
        conversation: Conversation,
        content: str,
    ) -> Message:
        user_message = Message(
            project_id=self._project_id,
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content=content,
        )
        conversation.last_message_at = datetime.now(UTC)
        self._message_repository.add(user_message)
        await self._message_repository.flush()
        await self._conversation_repository.flush()
        await self._session.commit()
        await self._session.refresh(user_message)
        await self._session.refresh(conversation)
        return user_message

    async def _commit_assistant_message(
        self,
        *,
        conversation: Conversation,
        content: str,
        finish_reason: str | None,
        input_tokens: int | None,
        output_tokens: int | None,
        prompt_version: str,
        provider: str,
        model: str,
        metadata: dict[str, Any],
        citations: list[dict],
        user_content_for_title: str,
    ) -> Message:
        await self._session.refresh(conversation)
        provider_override = provider if provider != conversation.provider else None
        model_override = model if model != conversation.model else None

        assistant = Message(
            project_id=self._project_id,
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content=content,
            finish_reason=finish_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prompt_version=prompt_version,
            embedding_set_version=self._retrieval_config.embedding_set_version,
            provider=provider_override,
            model=model_override,
            message_metadata=metadata,
            citations=citations,
        )
        conversation.last_message_at = datetime.now(UTC)
        if conversation.title is None:
            conversation.title = self._auto_title(user_content_for_title)

        self._message_repository.add(assistant)
        await self._message_repository.flush()
        await self._conversation_repository.flush()
        return await commit_refresh(self._session, assistant)

    def _auto_title(self, user_content: str) -> str:
        stripped = " ".join(user_content.split())
        max_len = self._chat_config.auto_title_max_chars
        if len(stripped) <= max_len:
            return stripped
        return f"{stripped[: max_len - 1].rstrip()}…"

    def _build_metadata(
        self,
        *,
        retrieval_ms: int,
        generation_ms: int,
        total_ms: int,
        retrieved_count: int,
        selected_count: int,
    ) -> dict[str, Any]:
        return {
            "retrieval_time_ms": retrieval_ms,
            "generation_time_ms": generation_ms,
            "total_time_ms": total_ms,
            "retrieval_strategy": self._retrieval_config.strategy.value,
            "retrieval_top_k": self._chat_config.retrieval_top_k,
            "retrieved_chunk_count": retrieved_count,
            "selected_chunk_count": selected_count,
        }

    def _llm_max_tokens(self) -> int:
        return self._llm_config.max_tokens

    async def _require_mutable_conversation(self, conversation_id: uuid.UUID) -> Conversation:
        conversation = await get_or_raise(
            self._conversation_repository,
            conversation_id,
            message=_NOT_FOUND["message"],
            code=_NOT_FOUND["code"],
            include_deleted=True,
        )
        require_not_deleted(conversation, **_DELETED)
        if not conversation.is_active:
            raise NotFoundError(
                message="Conversation is not active.",
                code="conversation_inactive",
            )
        return conversation

    def _to_response(
        self,
        message: Message,
        *,
        conversation_provider: str | None = None,
        conversation_model: str | None = None,
    ) -> MessageResponse:
        return MessageResponse.from_message(
            message,
            conversation_provider=conversation_provider,
            conversation_model=conversation_model,
        )
