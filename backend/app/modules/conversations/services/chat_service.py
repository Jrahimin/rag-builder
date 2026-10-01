"""RAG chat orchestration with split transaction boundaries."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from datetime import UTC, datetime
from typing import Any, cast

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import (
    ChatConfig,
    EvidenceGateMode,
    LLMConfig,
    RetrievalConfig,
    WebSearchConfig,
)
from app.core.exceptions import NotFoundError, ServiceUnavailableError
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.modules.conversations.answer_draft import public_draft_diagnostics
from app.modules.conversations.context_builder import (
    ContextBuilder,
)
from app.modules.conversations.grounding_service import (
    GroundingService,
)
from app.modules.conversations.ports import (
    ContextChunk,
    RetrievalPort,
)
from app.modules.conversations.prompt_builder import (
    PromptBuilder,
    PromptHistoryMessage,
)
from app.modules.conversations.repositories.conversation_repository import ConversationRepository
from app.modules.conversations.repositories.message_diagnostic_repository import (
    MessageDiagnosticRepository,
)
from app.modules.conversations.repositories.message_repository import MessageRepository
from app.modules.conversations.schemas.message import (
    ChatTurnResponse,
    MessageResponse,
    MessageSendRequest,
)
from app.modules.conversations.services.message_execution_runner_service import (
    ExecutionCancelled,
    MessageExecutionRunner,
    _accepted_web_evidence,  # noqa: F401 - compatibility exports
    _assemble_response_policy,  # noqa: F401 - compatibility exports
    _balanced_evidence,  # noqa: F401 - compatibility exports
    _bounded_recovery_profile,  # noqa: F401 - compatibility exports
    _citation_identities_from_message,  # noqa: F401 - compatibility exports
    _combine_token_counts,
    _combined_auxiliary_usage,  # noqa: F401 - compatibility exports
    _compact_resolution_summary,
    _complete_evidence_funnel,
    _context_trace_item,  # noqa: F401 - compatibility exports
    _coverage_scope_fallback,  # noqa: F401 - compatibility exports
    _coverage_verification_failed,  # noqa: F401 - compatibility exports
    _effective_scope_modifier_records,  # noqa: F401 - compatibility exports
    _has_governed_calculation_source,  # noqa: F401 - compatibility exports
    _has_governed_current_rule_source,  # noqa: F401 - compatibility exports
    _history_message_from_orm,
    _inherited_coverage_diagnostics,  # noqa: F401 - compatibility exports
    _is_resolver_history_row,
    _optional_date,  # noqa: F401 - compatibility exports
    _optional_int,
    _optional_str,  # noqa: F401 - compatibility exports
    _optional_uuid,
    _optional_uuid_or_none,  # noqa: F401 - compatibility exports
    _preparation_progress,
    _PreparedTurn,
    _prompt_history_for_generation,  # noqa: F401 - compatibility exports
    _prune_unverified_partial_paragraphs,  # noqa: F401 - compatibility exports
    _render_structured_answer,  # noqa: F401 - compatibility exports
    _repair_missing_citation_claims,  # noqa: F401 - compatibility exports
    _requires_answer_draft,  # noqa: F401 - compatibility exports
    _requires_calculation_coverage,  # noqa: F401 - compatibility exports
    _requires_current_rule_coverage,  # noqa: F401 - compatibility exports
    _reviewed_web_fallback_eligible,  # noqa: F401 - compatibility exports
    _scope_current_authority_status,  # noqa: F401 - compatibility exports
    _source_provenance,  # noqa: F401 - compatibility exports
    _sum_known_tokens,  # noqa: F401 - compatibility exports
    _web_context_chunks,  # noqa: F401 - compatibility exports
    _web_overview_partial_answer,  # noqa: F401 - compatibility exports
)
from app.modules.conversations.turn_resolution import (
    RequestFilters,
    normalize_request_scope,
)
from app.platform.audit.contracts import AuditActorType, AuditEventType, AuditOutcome, AuditRecorder
from app.platform.domain.lifecycle_service import get_or_raise, require_not_deleted
from app.platform.domain.transactions import commit_refresh
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.contracts.llm import BaseLLMProvider
from app.platform.providers.contracts.web_search import (
    BaseWebSearchProvider,
)
from app.platform.providers.errors import ProviderError, ProviderQuotaError, ProviderTimeoutError
from app.platform.providers.request_work import ObservedLLM, RequestWork

logger = structlog.get_logger(__name__)

type ShouldCancelFn = Callable[[], Awaitable[bool]]
type LLMProviderResolver = Callable[[Conversation], BaseLLMProvider]

_NOT_FOUND = {"message": "Conversation not found.", "code": "conversation_not_found"}
_DELETED = {"message": "Cannot modify a deleted conversation.", "code": "conversation_deleted"}
_WEB_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "do",
    "does",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
}


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
        embedder: BaseEmbeddingProvider | None = None,
        config_snapshot_id: uuid.UUID | None = None,
        config_provenance: dict[str, Any] | None = None,
        domain_instructions: str = "",
        prompt_profile: str = "default",
        web_search: BaseWebSearchProvider | None = None,
        web_search_config: WebSearchConfig | None = None,
        store_candidate_trace: bool | None = None,
        evidence_approach: str = "authoritative",
        translation_enabled: bool | None = None,
        work: RequestWork | None = None,
        diagnostic_capture: bool = False,
        diagnostic_actor_id: str = "system",
        diagnostic_repository: MessageDiagnosticRepository | None = None,
        audit: AuditRecorder | None = None,
    ) -> None:
        self._session = session
        self._diagnostic_capture = diagnostic_capture
        self._diagnostic_actor_id = diagnostic_actor_id
        self._diagnostic_repository = diagnostic_repository
        self._audit = audit
        self._work = work or RequestWork(project_id)
        self._evidence_approach = evidence_approach
        self._translation_enabled = translation_enabled
        self._project_id = project_id
        self._conversation_repository = conversation_repository
        self._message_repository = message_repository
        self._retrieval = retrieval
        self._chat_config = chat_config
        self._retrieval_config = retrieval_config
        self._llm_config = llm_config
        self._resolve_llm = resolve_llm
        self._config_snapshot_id = config_snapshot_id
        self._config_provenance = config_provenance or {}
        self._domain_instructions = domain_instructions
        self._prompt_profile = prompt_profile
        self._web_search = web_search
        self._web_search_config = web_search_config or WebSearchConfig()
        self._store_candidate_trace = (
            store_candidate_trace
            if store_candidate_trace is not None
            else chat_config.store_candidate_trace
        )
        self._context_builder = ContextBuilder(chat_config, evidence_approach=evidence_approach)
        self._prompt_builder = PromptBuilder()
        self._grounding = GroundingService(
            chat_config,
            embedder=self._work.wrap(embedder) if embedder is not None else None,
        )

        self._runner = MessageExecutionRunner(
            project_id=project_id,
            retrieval=retrieval,
            chat_config=chat_config,
            retrieval_config=retrieval_config,
            llm_config=llm_config,
            release_read_transaction=self._release_read_transaction,
            embedder=embedder,
            config_snapshot_id=config_snapshot_id,
            config_provenance=config_provenance,
            domain_instructions=domain_instructions,
            prompt_profile=prompt_profile,
            web_search=web_search,
            web_search_config=web_search_config,
            store_candidate_trace=store_candidate_trace,
            evidence_approach=evidence_approach,
            translation_enabled=translation_enabled,
            work=self._work,
        )

    async def send_message(
        self,
        conversation_id: uuid.UUID,
        request: MessageSendRequest,
    ) -> ChatTurnResponse:
        with self._work.attached():
            self._work.evidence_snapshot.update(
                {
                    "preview_index_build_id": str(request.preview_index_build_id)
                    if request.preview_index_build_id
                    else None,
                    "normalized_scope": normalize_request_scope(
                        request.content,
                        request_filters=RequestFilters(
                            document_id=request.document_id,
                            metadata_filter=dict(request.metadata_filter or {}),
                            as_of=request.as_of,
                            source_scope=request.source_scope,
                        ),
                    ).model_dump(mode="json"),
                }
            )
            self._work.deadline = min(self._work.deadline, self._work.started + 50.0)
            try:
                async with asyncio.timeout(max(0.0, self._work.deadline - time.perf_counter())):
                    return await self._deliver_send_message(conversation_id, request)
            except (TimeoutError, ProviderTimeoutError) as exc:
                conversation, user, assistant = await self._deadline_terminal(
                    conversation_id,
                    request,
                    failure=exc if isinstance(exc, ProviderTimeoutError) else None,
                )
                return ChatTurnResponse(
                    user_message=self._to_response(
                        user,
                        conversation_provider=conversation.provider,
                        conversation_model=conversation.model,
                    ),
                    assistant_message=self._to_response(
                        assistant,
                        conversation_provider=conversation.provider,
                        conversation_model=conversation.model,
                    ),
                )

    async def _deliver_send_message(
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
        await self._raise_preparation_failure(
            conversation, prepared, request.content, started=started, streamed=False
        )

        if prepared.non_knowledge_response is not None:
            assistant_message = await self._persist_assistant_turn(
                conversation=conversation,
                prepared=prepared,
                content=prepared.non_knowledge_response,
                finish_reason="conversation",
                input_tokens=0,
                output_tokens=0,
                provider=prepared.llm.provider_name,
                model=prepared.llm.model_name,
                generation_ms=0,
                total_ms=int((time.perf_counter() - started) * 1000),
                user_content_for_title=request.content,
                streamed=False,
                input_tokens_logged=0,
                output_tokens_logged=0,
                generation_ran=False,
                non_knowledge_turn=True,
            )
            return ChatTurnResponse(
                user_message=user_message_response,
                assistant_message=self._to_response(
                    assistant_message,
                    conversation_provider=conversation_provider,
                    conversation_model=conversation_model,
                ),
            )

        if prepared.clarification_response is not None:
            input_tokens, output_tokens = _combine_token_counts(prepared.resolver_usage, 0, 0)
            assistant_message = await self._persist_assistant_turn(
                conversation=conversation,
                prepared=prepared,
                content=prepared.clarification_response,
                finish_reason="clarification",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                provider=prepared.llm.provider_name,
                model=prepared.llm.model_name,
                generation_ms=0,
                total_ms=int((time.perf_counter() - started) * 1000),
                user_content_for_title=request.content,
                streamed=False,
                input_tokens_logged=input_tokens,
                output_tokens_logged=output_tokens,
                generation_ran=False,
                clarification_turn=True,
            )
            return ChatTurnResponse(
                user_message=user_message_response,
                assistant_message=self._to_response(
                    assistant_message,
                    conversation_provider=conversation_provider,
                    conversation_model=conversation_model,
                ),
            )

        if not prepared.selected:
            input_tokens, output_tokens = _combine_token_counts(prepared.resolver_usage, 0, 0)
            assistant_message = await self._persist_assistant_turn(
                conversation=conversation,
                prepared=prepared,
                content=self._insufficient_content(prepared, request.content),
                finish_reason="insufficient_evidence",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                provider=prepared.llm.provider_name,
                model=prepared.llm.model_name,
                generation_ms=0,
                total_ms=int((time.perf_counter() - started) * 1000),
                user_content_for_title=request.content,
                streamed=False,
                input_tokens_logged=input_tokens,
                output_tokens_logged=output_tokens,
                insufficient_reason=prepared.evidence.reason,
                generation_ran=False,
            )
            return ChatTurnResponse(
                user_message=user_message_response,
                assistant_message=self._to_response(
                    assistant_message,
                    conversation_provider=conversation_provider,
                    conversation_model=conversation_model,
                ),
            )

        generation_started = time.perf_counter()
        try:
            completion = await self._runner.generate(prepared)
        except ProviderError as exc:
            if isinstance(exc, ProviderTimeoutError):
                raise
            await self._record_failed_execution(
                conversation=conversation,
                prepared=prepared,
                exc=exc,
                content="",
                generation_ms=int((time.perf_counter() - generation_started) * 1000),
                total_ms=int((time.perf_counter() - started) * 1000),
                user_content_for_title=request.content,
                streamed=False,
            )
            self._log_provider_failure(conversation_id, exc)
            raise self._provider_unavailable(exc) from exc

        generation_ms = int((time.perf_counter() - generation_started) * 1000)
        total_ms = int((time.perf_counter() - started) * 1000)
        content = completion.content

        input_tokens, output_tokens = _combine_token_counts(
            prepared.resolver_usage,
            completion.usage.input_tokens,
            completion.usage.output_tokens,
        )
        assistant_message = await self._persist_assistant_turn(
            conversation=conversation,
            prepared=prepared,
            content=content,
            finish_reason=completion.finish_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider=completion.provider,
            model=completion.model,
            generation_ms=generation_ms,
            total_ms=total_ms,
            user_content_for_title=request.content,
            streamed=False,
            input_tokens_logged=input_tokens,
            output_tokens_logged=output_tokens,
            generation_ran=True,
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
        with self._work.attached():
            self._work.evidence_snapshot.update(
                {
                    "preview_index_build_id": str(request.preview_index_build_id)
                    if request.preview_index_build_id
                    else None,
                    "normalized_scope": normalize_request_scope(
                        request.content,
                        request_filters=RequestFilters(
                            document_id=request.document_id,
                            metadata_filter=dict(request.metadata_filter or {}),
                            as_of=request.as_of,
                            source_scope=request.source_scope,
                        ),
                    ).model_dump(mode="json"),
                }
            )
            self._work.deadline = min(self._work.deadline, self._work.started + 50.0)
            try:
                async with asyncio.timeout(max(0.0, self._work.deadline - time.perf_counter())):
                    async with aclosing(
                        cast(
                            AsyncGenerator[str | dict[str, Any], None],
                            self._deliver_stream_message(
                                conversation_id, request, should_cancel=should_cancel
                            ),
                        )
                    ) as delivery:
                        async for item in delivery:
                            yield item
            except (TimeoutError, ProviderTimeoutError) as exc:
                if should_cancel is not None and await should_cancel():
                    return
                conversation, _, assistant = await self._deadline_terminal(
                    conversation_id,
                    request,
                    failure=exc if isinstance(exc, ProviderTimeoutError) else None,
                )
                yield assistant.content
                yield self._done_event(assistant, conversation)

    async def _deadline_terminal(
        self,
        conversation_id: uuid.UUID,
        request: MessageSendRequest,
        *,
        failure: ProviderTimeoutError | None = None,
    ) -> tuple[Conversation, Message, Message]:
        """Reserve the last ten seconds for a deterministic persisted limitation."""
        cause = (
            failure.context.get("reason") if failure is not None else "request_deadline_exceeded"
        )
        if cause not in {"request_deadline_exceeded", "recovery_deadline_exceeded"}:
            cause = "provider_timeout"
        try:
            async with asyncio.timeout(max(0.0, self._work.request_deadline - time.perf_counter())):
                await self._session.rollback()
                conversation = await self._require_mutable_conversation(conversation_id)
                user = getattr(self, "_deadline_user_message", None)
                if user is None:
                    user = await self._commit_user_message(conversation, request.content)
                else:
                    await self._session.refresh(user)
                result = self._runner.deadline_result(
                    request,
                    reason=str(cause),
                    failure_phase=str(failure.context["phase"])
                    if failure and failure.context.get("phase")
                    else None,
                )
                outcome = result.finalization.terminal
                content, finish_reason, reason = (
                    result.content,
                    result.finish_reason,
                    result.insufficient_evidence_reason,
                )
                notices = result.metadata["notices"]
                assistant = await self._commit_assistant_message(
                    conversation=conversation,
                    content=content,
                    finish_reason=finish_reason,
                    input_tokens=None,
                    output_tokens=None,
                    prompt_version="deadline.terminal.v1",
                    provider=conversation.provider or self._llm_config.backend.value,
                    model=conversation.model or self._llm_config.model,
                    metadata={
                        **result.metadata,
                        "retrieval_time_ms": self._work.timings.get(
                            "initial_retrieval", self._work.timings.get("retrieval", 0)
                        ),
                        "generation_time_ms": self._work.timings.get("answer_generation", 0),
                        "total_time_ms": round((time.perf_counter() - self._work.started) * 1000),
                        "terminal_result": "exhausted_budget",
                        "evidence_summary": {
                            "coverage": "incomplete",
                            "claim_verification": "unverified",
                        },
                        "lifecycle": self._work.snapshot(),
                        "terminal_outcome": outcome.model_dump(mode="json"),
                        "notices": notices,
                    },
                    citations=[],
                    claims=[],
                    grounded=None,
                    insufficient_evidence_reason=reason,
                    user_content_for_title=request.content,
                )
                return conversation, user, assistant
        except TimeoutError as exc:
            raise self._provider_unavailable(
                ProviderTimeoutError(
                    "Persistence exceeded the shared request deadline.",
                    provider_name="persistence",
                    context={"reason": "request_deadline_exceeded"},
                )
            ) from exc

    async def _deliver_stream_message(
        self,
        conversation_id: uuid.UUID,
        request: MessageSendRequest,
        *,
        should_cancel: ShouldCancelFn | None = None,
    ) -> AsyncIterator[str | dict[str, Any]]:
        conversation = await self._require_mutable_conversation(conversation_id)
        started = time.perf_counter()

        user_message = await self._commit_user_message(conversation, request.content)
        yield {
            "event": "progress",
            "stage": "understanding_request",
            "message": "Understanding request",
        }
        preparation = asyncio.create_task(
            self._prepare_turn(
                conversation=conversation,
                conversation_id=conversation_id,
                user_message=user_message,
                request=request,
            )
        )
        progress_stage = "understanding_request"
        try:
            while not preparation.done():
                await asyncio.wait({preparation}, timeout=1)
                if should_cancel is not None and await should_cancel():
                    return
                next_stage, next_message = _preparation_progress(self._work)
                if next_stage != progress_stage:
                    progress_stage = next_stage
                    yield {
                        "event": "progress",
                        "stage": progress_stage,
                        "message": next_message,
                    }
            prepared = await preparation
        finally:
            if not preparation.done():
                preparation.cancel()
            await asyncio.gather(preparation, return_exceptions=True)
        await self._raise_preparation_failure(
            conversation, prepared, request.content, started=started, streamed=True
        )
        yield {
            "event": "progress",
            "stage": "generating_answer",
            "message": "Preparing your answer",
        }

        if should_cancel is not None and await should_cancel():
            return

        if prepared.non_knowledge_response is not None:
            content = prepared.non_knowledge_response
            yield content
            assistant_message = await self._persist_assistant_turn(
                conversation=conversation,
                prepared=prepared,
                content=content,
                finish_reason="conversation",
                input_tokens=0,
                output_tokens=0,
                provider=prepared.llm.provider_name,
                model=prepared.llm.model_name,
                generation_ms=0,
                total_ms=int((time.perf_counter() - started) * 1000),
                user_content_for_title=request.content,
                streamed=True,
                input_tokens_logged=0,
                output_tokens_logged=0,
                generation_ran=False,
                non_knowledge_turn=True,
            )
            yield self._done_event(assistant_message, conversation)
            return

        if prepared.clarification_response is not None:
            content = prepared.clarification_response
            yield content
            input_tokens, output_tokens = _combine_token_counts(prepared.resolver_usage, 0, 0)
            assistant_message = await self._persist_assistant_turn(
                conversation=conversation,
                prepared=prepared,
                content=content,
                finish_reason="clarification",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                provider=prepared.llm.provider_name,
                model=prepared.llm.model_name,
                generation_ms=0,
                total_ms=int((time.perf_counter() - started) * 1000),
                user_content_for_title=request.content,
                streamed=True,
                input_tokens_logged=input_tokens,
                output_tokens_logged=output_tokens,
                generation_ran=False,
                clarification_turn=True,
            )
            yield self._done_event(assistant_message, conversation)
            return

        if not prepared.selected:
            content = self._insufficient_content(prepared, request.content)
            yield content
            input_tokens, output_tokens = _combine_token_counts(prepared.resolver_usage, 0, 0)
            assistant_message = await self._persist_assistant_turn(
                conversation=conversation,
                prepared=prepared,
                content=content,
                finish_reason="insufficient_evidence",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                provider=prepared.llm.provider_name,
                model=prepared.llm.model_name,
                generation_ms=0,
                total_ms=int((time.perf_counter() - started) * 1000),
                user_content_for_title=request.content,
                streamed=True,
                input_tokens_logged=input_tokens,
                output_tokens_logged=output_tokens,
                insufficient_reason=prepared.evidence.reason,
                generation_ran=False,
            )
            yield self._done_event(assistant_message, conversation)
            return

        generation_started = time.perf_counter()
        try:
            completion = await self._runner.generate(
                prepared, streamed=True, should_cancel=should_cancel
            )
        except ExecutionCancelled:
            return
        except ProviderError as exc:
            if isinstance(exc, ProviderTimeoutError):
                raise
            await self._record_failed_execution(
                conversation=conversation,
                prepared=prepared,
                exc=exc,
                content=self._runner._generation_content,
                generation_ms=int((time.perf_counter() - generation_started) * 1000),
                total_ms=int((time.perf_counter() - started) * 1000),
                user_content_for_title=request.content,
                streamed=True,
            )
            raise self._provider_unavailable(exc) from exc
        if should_cancel is not None and await should_cancel():
            return
        generation_ms = int((time.perf_counter() - generation_started) * 1000)
        total_ms = int((time.perf_counter() - started) * 1000)
        full_content = completion.content
        finish_reason = completion.finish_reason
        input_tokens, output_tokens = _combine_token_counts(
            prepared.resolver_usage, completion.usage.input_tokens, completion.usage.output_tokens
        )
        yield {
            "event": "progress",
            "stage": "verifying_citations",
            "message": "Verifying citations",
        }
        assistant_message = await self._persist_assistant_turn(
            conversation=conversation,
            prepared=prepared,
            content=full_content,
            finish_reason=finish_reason or "stop",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider=prepared.llm.provider_name,
            model=prepared.llm.model_name,
            generation_ms=generation_ms,
            total_ms=total_ms,
            user_content_for_title=request.content,
            streamed=True,
            input_tokens_logged=input_tokens,
            output_tokens_logged=output_tokens,
            generation_ran=True,
        )

        yield assistant_message.content

        yield self._done_event(assistant_message, conversation)

    async def _prepare_turn(
        self,
        *,
        conversation: Conversation,
        conversation_id: uuid.UUID,
        user_message: Message,
        request: MessageSendRequest,
    ) -> _PreparedTurn:
        # Transport-owned injections may be replaced by a host/test between turns.
        # Freeze their current values in the execution runner before starting this turn.
        for name in (
            "_retrieval",
            "_chat_config",
            "_retrieval_config",
            "_llm_config",
            "_web_search",
            "_web_search_config",
            "_store_candidate_trace",
            "_evidence_approach",
            "_translation_enabled",
            "_domain_instructions",
            "_prompt_profile",
        ):
            setattr(self._runner, name, getattr(self, name))
        preparation_started = time.perf_counter()
        history_limit = self._chat_config.max_history_messages
        if history_limit <= 0:
            loaded: list[Message] = []
        else:
            loaded = await self._message_repository.list_recent_for_conversation(
                conversation_id,
                limit=history_limit,
                before_created_at=user_message.created_at,
                before_id=user_message.id,
            )
        loaded = [message for message in loaded if message.id != user_message.id]
        citation_chunks = {
            message.id: list(message.citations or [])
            for message in loaded
            if message.role is MessageRole.ASSISTANT
        }
        previous_assistant_row = next(
            (message for message in reversed(loaded) if message.role is MessageRole.ASSISTANT),
            None,
        )
        # Capture ORM-backed fields before closing the read transaction.
        # rollback() expires identity-mapped rows; later exact-recall must not
        # lazy-load message_metadata (MissingGreenlet outside a greenlet).
        generation_history = [
            PromptHistoryMessage(role=message.role, content=message.content) for message in loaded
        ]
        resolver_source = [
            _history_message_from_orm(message)
            for message in loaded
            if _is_resolver_history_row(message)
        ]
        assistant_metadata_by_id = {
            str(message.id): dict(message.message_metadata or {})
            for message in loaded
            if message.role is MessageRole.ASSISTANT
        }
        previous_assistant_metadata = (
            dict(previous_assistant_row.message_metadata or {})
            if previous_assistant_row is not None
            else {}
        )
        current_message_id = user_message.id
        provider = self._resolve_llm(conversation)
        llm = ObservedLLM(
            provider,
            self._work,
            capacity=self._llm_config.model_context_windows.get(
                provider.model_name, self._llm_config.context_window_tokens
            ),
        )
        temperature = self._effective_temperature(conversation)
        await self._release_read_transaction()

        self._work.timings["history_loading"] += round(
            (time.perf_counter() - preparation_started) * 1000
        )
        return await self._runner.prepare(
            request=request,
            current_message_id=current_message_id,
            generation_history=generation_history,
            resolver_source=resolver_source,
            citation_chunks=citation_chunks,
            assistant_metadata_by_id=assistant_metadata_by_id,
            previous_assistant_metadata=previous_assistant_metadata,
            llm=llm,
            temperature=temperature,
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
        insufficient_reason: object | None = None,
        generation_ran: bool = False,
        non_knowledge_turn: bool = False,
        clarification_turn: bool = False,
    ) -> Message:
        result = await self._runner.finalize(
            prepared=prepared,
            content=content,
            finish_reason=finish_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider=provider,
            model=model,
            generation_ms=generation_ms,
            total_ms=total_ms,
            user_content_for_title=user_content_for_title,
            streamed=streamed,
            input_tokens_logged=input_tokens_logged,
            output_tokens_logged=output_tokens_logged,
            insufficient_reason=insufficient_reason,
            generation_ran=generation_ran,
            non_knowledge_turn=non_knowledge_turn,
            clarification_turn=clarification_turn,
        )
        persistence_started = time.perf_counter()
        assistant = await self._commit_assistant_message(
            conversation=conversation,
            **result.persistence_payload(),
            user_content_for_title=user_content_for_title,
        )
        self._work.timings["persistence"] += round(
            (time.perf_counter() - persistence_started) * 1000
        )
        return assistant

    async def _correct_answer_draft(
        self, prepared: _PreparedTurn, content: str, failure: tuple[str, dict[str, Any]]
    ) -> tuple[str, dict[str, Any]]:
        return await self._runner._correct_answer_draft(
            prepared=prepared, content=content, failure=failure
        )

    async def _raise_preparation_failure(
        self,
        conversation: Conversation,
        prepared: _PreparedTurn,
        user_content: str,
        *,
        started: float,
        streamed: bool,
    ) -> None:
        if prepared.preparation_error is None:
            return
        error = prepared.preparation_error
        public_error = self._provider_unavailable(error)
        await self._record_failed_execution(
            conversation=conversation,
            prepared=prepared,
            exc=error,
            content=public_error.message,
            generation_ms=0,
            total_ms=int((time.perf_counter() - started) * 1000),
            user_content_for_title=user_content,
            streamed=streamed,
        )
        self._log_provider_failure(conversation.id, error)
        raise public_error from error

    async def _record_failed_execution(
        self,
        *,
        conversation: Conversation,
        prepared: _PreparedTurn,
        exc: ProviderError,
        content: str,
        generation_ms: int,
        total_ms: int,
        user_content_for_title: str,
        streamed: bool,
    ) -> None:
        """Persist a safe usage/error record without replacing the provider failure."""
        if not content.strip():
            content = self._provider_unavailable(exc).message
        metadata = self._build_metadata(
            retrieval_ms=prepared.retrieval_ms,
            generation_ms=generation_ms,
            total_ms=total_ms,
            retrieved_count=len(prepared.chunks),
            selected_count=len(prepared.selected),
            retrieval_diagnostics=prepared.retrieval_diagnostics,
            selected_chunks=prepared.selected,
        )
        metadata.update(
            {
                "response_mode": self._chat_config.response_mode.value,
                "response_policy": prepared.response_policy,
                "source_provenance": prepared.source_provenance.value,
                "web_search": prepared.web_search_diagnostics,
                "execution_status": "failed",
                "execution_error_code": exc.code,
                "execution_retryable": exc.retryable,
                "grounded": False,
                "citation_coverage": 0.0,
            }
        )
        if prepared.turn_resolution is not None:
            metadata["turn_resolution"] = prepared.turn_resolution
        failed_funnel = _complete_evidence_funnel(
            prepared.retrieval_diagnostics.get("evidence_funnel"),
            evidence=prepared.evidence,
            retrieved_count=len(prepared.chunks),
            selected_count=len(prepared.selected),
            citations=[],
            claims=[],
            rerank_status=prepared.retrieval_diagnostics.get("rerank_status"),
            blocked=False,
            generation_ran=False,
            observe=self._chat_config.evidence_gate_mode is EvidenceGateMode.OBSERVE,
            non_knowledge_turn=prepared.non_knowledge_response is not None,
        )
        failed_funnel["outcome"] = "failed"
        metadata["evidence_funnel"] = failed_funnel
        try:
            await self._commit_assistant_message(
                conversation=conversation,
                content=content,
                finish_reason="error",
                input_tokens=None,
                output_tokens=None,
                prompt_version=prepared.prompt_version,
                provider=exc.provider_name or prepared.llm.provider_name,
                model=prepared.llm.model_name,
                metadata=metadata,
                citations=[],
                claims=[],
                grounded=False,
                insufficient_evidence_reason=None,
                user_content_for_title=user_content_for_title,
            )
        except Exception:
            await self._session.rollback()
            logger.exception(
                "chat_failure_record_persist_failed",
                project_id=str(self._project_id),
                conversation_id=str(conversation.id),
                provider=exc.provider_name,
                streamed=streamed,
            )

    def _done_event(self, message: Message, conversation: Conversation) -> dict[str, Any]:
        response = self._to_response(
            message,
            conversation_provider=conversation.provider,
            conversation_model=conversation.model,
        )
        return {
            "event": "done",
            "assistant_message_id": str(message.id),
            "citations": [item.model_dump(mode="json") for item in response.citations],
            "claims": [item.model_dump(mode="json") for item in response.claims],
            "grounded": response.grounded,
            "insufficient_evidence_reason": response.insufficient_evidence_reason,
            "notices": [item.model_dump(mode="json") for item in response.notices],
            "response_mode": self._chat_config.response_mode.value,
            "source_provenance": response.source_provenance.value,
            "web_search": response.metadata.get("web_search", {}),
            "finish_reason": response.finish_reason,
            "terminal_outcome": response.terminal_outcome.model_dump(mode="json")
            if response.terminal_outcome
            else None,
            "lifecycle": response.metadata.get("lifecycle", {}),
            "execution": response.metadata.get("execution", {}),
            "turn_resolution": _compact_resolution_summary(response.metadata),
        }

    def _citations_for(
        self,
        selected: list[ContextChunk],
        *,
        prompt_version: str,
        evidence_scope: dict[str, Any] | None = None,
        originating_assistant_message_id: uuid.UUID | None = None,
        inherited_coverage: dict[str, Any] | None = None,
        knowledge_repair: dict[str, Any] | None = None,
        expansion_records: list[dict[str, Any]] | None = None,
        recalled_chunks: list[ContextChunk] | None = None,
    ) -> list[dict]:
        return self._runner._citations_for(
            selected=selected,
            prompt_version=prompt_version,
            evidence_scope=evidence_scope,
            originating_assistant_message_id=originating_assistant_message_id,
            inherited_coverage=inherited_coverage,
            knowledge_repair=knowledge_repair,
            expansion_records=expansion_records,
            recalled_chunks=recalled_chunks,
        )

    def _insufficient_content(self, prepared: _PreparedTurn, question: str) -> str:
        return MessageExecutionRunner._insufficient_content(
            self, prepared=prepared, question=question
        )

    # _with_web_fallback_notice / _web_fallback_notice removed in Phase 3.
    # Web evidence is now announced via a structured Notice, not prepended text.

    async def _release_read_transaction(self) -> None:
        """Close any implicit read transaction before slow external I/O."""
        await self._session.rollback()

    def _effective_temperature(self, conversation: Conversation) -> float | None:
        if conversation.temperature is not None:
            return conversation.temperature
        return self._llm_config.temperature

    def _provider_unavailable(self, exc: ProviderError) -> ServiceUnavailableError:
        if exc.context.get("reason") in {
            "evidence_review_timeout",
            "recovery_deadline_exceeded",
        }:
            return ServiceUnavailableError(
                message="Evidence review reached its time limit before an answer could be "
                "verified. Try one part of the question at a time.",
                code="evidence_review_timeout",
                context={"retryable": True},
            )
        if isinstance(exc, ProviderQuotaError):
            return ServiceUnavailableError(
                message="The language model provider has no API credits available. "
                "Contact the project administrator to restore provider billing.",
                code="llm_provider_quota_exhausted",
                context={"provider": exc.provider_name, "retryable": False},
            )
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
            citations=[],
            claims=[],
            config_snapshot_id=self._config_snapshot_id,
            config_provenance=self._config_provenance,
        )
        conversation.last_message_at = datetime.now(UTC)
        self._message_repository.add(user_message)
        await self._message_repository.flush()
        await self._conversation_repository.flush()
        await self._session.commit()
        await self._session.refresh(user_message)
        await self._session.refresh(conversation)
        self._deadline_user_message = user_message
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
        claims: list[dict],
        grounded: bool | None,
        insufficient_evidence_reason: str | None,
        user_content_for_title: str,
    ) -> Message:
        await self._session.refresh(conversation)
        provider_override = provider if provider != conversation.provider else None
        model_override = model if model != conversation.model else None

        metadata = dict(metadata)
        metadata["prompt_version"] = prompt_version
        metadata["provider_route"] = {"provider": provider, "model": model}
        diagnostic_payload = metadata.pop("operator_diagnostic", None)
        if "answer_draft" in metadata:
            metadata["answer_draft"] = public_draft_diagnostics(metadata["answer_draft"])
        metadata["lifecycle"] = {**metadata.get("lifecycle", {}), "persistence_completed": True}
        assistant = Message(
            project_id=self._project_id,
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content=content,
            finish_reason=finish_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prompt_version=prompt_version,
            embedding_set_version=_optional_int(metadata.get("embedding_set_version"))
            or self._retrieval_config.embedding_set_version,
            provider=provider_override,
            model=model_override,
            config_snapshot_id=self._config_snapshot_id,
            config_provenance=self._config_provenance,
            message_metadata=metadata,
            index_build_id=_optional_uuid(metadata.get("index_build_id")),
            source_metadata_generation=_optional_int(metadata.get("source_metadata_generation")),
            retrieval_latency_ms=_optional_int(metadata.get("retrieval_time_ms")),
            provider_latency_ms=_optional_int(metadata.get("generation_time_ms")),
            total_latency_ms=_optional_int(metadata.get("total_time_ms")),
            citations=citations,
            claims=claims,
            grounded=grounded,
            insufficient_evidence_reason=insufficient_evidence_reason,
        )
        conversation.last_message_at = datetime.now(UTC)
        if conversation.title is None:
            conversation.title = self._auto_title(user_content_for_title)

        self._message_repository.add(assistant)
        await self._message_repository.flush()
        await self._conversation_repository.flush()
        from app.modules.conversations.services.message_diagnostic_service import diagnostic_record

        if self._diagnostic_repository is not None:
            await self._diagnostic_repository.expire_payloads()
            self._diagnostic_repository.add(
                diagnostic_record(
                    project_id=self._project_id,
                    message_id=assistant.id,
                    metadata=metadata,
                    full_capture=self._diagnostic_capture,
                    payload=diagnostic_payload,
                )
            )
            if self._diagnostic_capture and self._audit is not None:
                self._audit.record(
                    event_type=AuditEventType.MESSAGE_DIAGNOSTIC_CAPTURE,
                    actor_type=AuditActorType.OPERATOR,
                    actor_id=self._diagnostic_actor_id,
                    resource_type="message_diagnostic",
                    resource_id=assistant.id,
                    outcome=AuditOutcome.SUCCESS,
                )
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
        retrieval_diagnostics: dict[str, Any],
        selected_chunks: list[ContextChunk],
    ) -> dict[str, Any]:
        return self._runner._build_metadata(
            retrieval_ms=retrieval_ms,
            generation_ms=generation_ms,
            total_ms=total_ms,
            retrieved_count=retrieved_count,
            selected_count=selected_count,
            retrieval_diagnostics=retrieval_diagnostics,
            selected_chunks=selected_chunks,
        )

    def _llm_max_tokens(self) -> int:
        return self._runner._llm_max_tokens()

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
