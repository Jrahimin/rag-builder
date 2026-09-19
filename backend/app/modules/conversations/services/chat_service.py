"""RAG chat orchestration with split transaction boundaries."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import (
    ChatConfig,
    EvidenceGateMode,
    LLMConfig,
    ResponseMode,
    RetrievalConfig,
    WebSearchConfig,
)
from app.core.exceptions import NotFoundError, ServiceUnavailableError
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.modules.conversations import turn_resolution as turn_resolution_mod
from app.modules.conversations.citation_snapshots import build_citation_snapshots
from app.modules.conversations.context_builder import (
    ContextBuilder,
    comparison_requested,
    compliance_overview_requested,
    historical_scope_requested,
    reviewed_work_count,
)
from app.modules.conversations.current_authority import cited_authority_summary
from app.modules.conversations.grounded_context import (
    assess_and_select_knowledge,
    reconstruct_reused_evidence,
    reused_evidence_decision,
    select_exact_recalled_knowledge,
)
from app.modules.conversations.grounding_service import (
    EvidenceDecision,
    GroundingResult,
    GroundingService,
)
from app.modules.conversations.notices import (
    Notice,
    insufficient_evidence_notice,
    scope_excludes_effective_modifier_notice,
    unresolved_authority_notice,
    web_evidence_used_notice,
)
from app.modules.conversations.ports import (
    ContextChunk,
    ContextRetrievalResult,
    EvidenceUnit,
    RetrievalPort,
)
from app.modules.conversations.prompt_builder import (
    PromptBuilder,
    PromptHistoryMessage,
    resolve_response_language,
)
from app.modules.conversations.prompts.registry import (
    GROUNDED_PROMPT_VERSION,
    PromptTemplate,
    require_prompt_template,
)
from app.modules.conversations.repositories.conversation_repository import ConversationRepository
from app.modules.conversations.repositories.message_repository import MessageRepository
from app.modules.conversations.schemas.message import (
    ChatTurnResponse,
    CitationSourceKind,
    InsufficientEvidenceReason,
    MessageResponse,
    MessageSendRequest,
    SourceProvenance,
)
from app.modules.conversations.services.evidence_repair_service import repair_knowledge_evidence
from app.modules.conversations.services.rewrite_retrieval import (
    citation_scopes_conflict,
    citations_have_exact_reuse_provenance,
    preceding_assistant,
    request_scope_conflicts,
    retained_rewrite_question,
    retrieve_rewrite_context,
    rewrite_citation_ids,
    rewrite_followup_mode,
    saved_evidence_scope,
    try_presentation_preflight,
    used_citation_items,
    used_citations_include_web,
)
from app.modules.conversations.services.web_evidence_review import (
    review_web_evidence,
    scoped_web_query,
)
from app.modules.conversations.turn_resolution import (
    RESOLUTION_HISTORY_CHAR_BUDGET,
    RESOLUTION_HISTORY_MESSAGE_CAP,
    RESOLUTION_MAX_OUTPUT_TOKENS,
    RESOLUTION_TIMEOUT_SECONDS,
    CitationIdentity,
    FollowupMode,
    HistoryMessage,
    RequestFilters,
    TurnOutcome,
    TurnRelation,
    TurnResolutionInput,
    bound_resolution_history,
)
from app.modules.conversations.turn_resolver import TurnResolver, bypass_resolution
from app.platform.domain.content_hash import content_hash
from app.platform.domain.language_detection import detect_language
from app.platform.domain.lifecycle_service import get_or_raise, require_not_deleted
from app.platform.domain.text_tokenization import tokenize
from app.platform.domain.transactions import commit_refresh
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.contracts.llm import BaseLLMProvider, ChatMessage, ChatUsage
from app.platform.providers.contracts.web_search import (
    BaseWebSearchProvider,
    WebSearchEvidence,
)
from app.platform.providers.errors import ProviderError, ProviderQuotaError
from app.platform.providers.prompt_budget import prompt_budget
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


@dataclass(frozen=True, slots=True)
class _PreparedTurn:
    """Retrieved context and prompt messages ready for generation."""

    prompt_version: str
    template: PromptTemplate
    selected: list[ContextChunk]
    knowledge_selected: list[ContextChunk]
    chunks: list[ContextChunk]
    history: list[PromptHistoryMessage]
    messages: list[ChatMessage]
    temperature: float | None
    llm: BaseLLMProvider
    retrieval_ms: int
    evidence: EvidenceDecision
    retrieval_diagnostics: dict[str, Any]
    grounding: GroundingService
    source_provenance: SourceProvenance
    web_search_diagnostics: dict[str, Any]
    response_language: str
    web_fallback_used: bool = False
    non_knowledge_response: str | None = None
    clarification_response: str | None = None
    scope_current_authority: dict[str, Any] | None = None
    notices: tuple[Notice, ...] = ()
    turn_resolution: dict[str, Any] | None = None
    resolver_usage: ChatUsage | None = None
    resolver_latency_ms: int = 0
    preparation_error: ProviderError | None = None
    evidence_scope: dict[str, Any] = field(default_factory=dict)
    originating_assistant_message_id: uuid.UUID | None = None
    inherited_coverage: dict[str, Any] | None = None
    response_policy: dict[str, Any] = field(default_factory=dict)


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
    ) -> None:
        self._session = session
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

    async def send_message(
        self,
        conversation_id: uuid.UUID,
        request: MessageSendRequest,
    ) -> ChatTurnResponse:
        with self._work.attached():
            return await self._deliver_send_message(conversation_id, request)

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
            with self._work.stage("answer_generation"):
                completion = await prepared.llm.generate(
                    prepared.messages,
                    temperature=prepared.temperature,
                    max_tokens=self._llm_max_tokens(),
                )
        except ProviderError as exc:
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
            async for item in self._deliver_stream_message(
                conversation_id, request, should_cancel=should_cancel
            ):
                yield item

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
        content_parts: list[str] = []
        finish_reason: str | None = None
        final_usage: ChatUsage | None = None
        generation_span = self._work.begin_span("answer_generation")
        generation_outcome = "completed"
        generation_error: BaseException | None = None
        cancelled_by_client = False
        answer_stream = prepared.llm.stream(
            prepared.messages,
            temperature=prepared.temperature,
            max_tokens=self._llm_max_tokens(),
        )

        try:
            async for chunk in answer_stream:
                if should_cancel is not None and await should_cancel():
                    cancelled_by_client = True
                    break
                if chunk.delta:
                    content_parts.append(chunk.delta)
                    yield chunk.delta
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
                if chunk.usage is not None:
                    final_usage = chunk.usage
        except ProviderError as exc:
            generation_outcome = "failed"
            generation_error = exc
            self._work.finish_span(
                generation_span, outcome=generation_outcome, error=generation_error
            )
            await self._record_failed_execution(
                conversation=conversation,
                prepared=prepared,
                exc=exc,
                content="".join(content_parts),
                generation_ms=int((time.perf_counter() - generation_started) * 1000),
                total_ms=int((time.perf_counter() - started) * 1000),
                user_content_for_title=request.content,
                streamed=True,
            )
            self._log_provider_failure(conversation_id, exc)
            raise self._provider_unavailable(exc) from exc
        except (asyncio.CancelledError, GeneratorExit) as exc:
            self._work.finish_span(generation_span, outcome="cancelled", error=exc)
            raise
        else:
            if cancelled_by_client or (should_cancel is not None and await should_cancel()):
                cancelled_by_client = True
                generation_outcome = "cancelled"
            self._work.finish_span(
                generation_span, outcome=generation_outcome, error=generation_error
            )
        finally:
            await answer_stream.aclose()

        if cancelled_by_client or (should_cancel is not None and await should_cancel()):
            return

        generation_ms = int((time.perf_counter() - generation_started) * 1000)
        total_ms = int((time.perf_counter() - started) * 1000)
        full_content = "".join(content_parts)
        generation_input = final_usage.input_tokens if final_usage is not None else None
        generation_output = final_usage.output_tokens if final_usage is not None else None
        input_tokens, output_tokens = _combine_token_counts(
            prepared.resolver_usage,
            generation_input,
            generation_output,
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

        yield self._done_event(assistant_message, conversation)

    async def _prepare_turn(
        self,
        *,
        conversation: Conversation,
        conversation_id: uuid.UUID,
        user_message: Message,
        request: MessageSendRequest,
    ) -> _PreparedTurn:
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
        current_content = request.content
        non_knowledge_response = _non_knowledge_response(current_content)
        prompt_version = GROUNDED_PROMPT_VERSION
        template = require_prompt_template(
            prompt_version, evidence_approach=self._evidence_approach
        )
        provider = self._resolve_llm(conversation)
        llm = ObservedLLM(
            provider,
            self._work,
            capacity=self._llm_config.model_context_windows.get(
                provider.model_name, self._llm_config.context_window_tokens
            ),
        )
        temperature = self._effective_temperature(conversation)
        request_filters = RequestFilters(
            document_id=request.document_id,
            metadata_filter=dict(request.metadata_filter or {}),
            as_of=request.as_of,
        )
        await self._release_read_transaction()

        self._work.timings["history_loading"] += round(
            (time.perf_counter() - preparation_started) * 1000
        )
        resolution_started = time.perf_counter()

        bounded_history, history_truncated = bound_resolution_history(
            resolver_source,
            max_messages=min(history_limit, RESOLUTION_HISTORY_MESSAGE_CAP),
            max_chars=RESOLUTION_HISTORY_CHAR_BUDGET,
        )
        payload = TurnResolutionInput(
            current_message_id=current_message_id,
            current_message=current_content,
            history=bounded_history,
            citation_metadata=[
                citation for message in bounded_history for citation in message.citations
            ],
            request_filters=request_filters,
            reference_time=turn_resolution_mod.utc_reference_datetime(),
            domain_instructions=self._domain_instructions,
        )
        preflight = None
        if non_knowledge_response is not None:
            resolved = bypass_resolution(payload, reason="casual_turn")
        elif not bounded_history and not (
            request.as_of is None
            and historical_scope_requested(current_content, self._evidence_approach)
        ):
            resolved = bypass_resolution(payload, reason="no_usable_history")
        else:
            preflight = try_presentation_preflight(
                payload,
                citations_by_message=citation_chunks,
            )
            if preflight is not None:
                resolved = preflight
            else:
                self._work.counts["resolver_calls"] += 1
                with self._work.stage("turn_resolution"):
                    resolved = await TurnResolver(
                        llm,
                        timeout_seconds=min(
                            RESOLUTION_TIMEOUT_SECONDS,
                            self._llm_config.request_timeout_seconds,
                        ),
                        max_output_tokens=min(
                            RESOLUTION_MAX_OUTPUT_TOKENS,
                            self._llm_max_tokens(),
                        ),
                    ).resolve(payload)

        self._work.timings["resolution"] += round((time.perf_counter() - resolution_started) * 1000)
        diagnostics = {
            **resolved.diagnostics,
            "history_truncated": history_truncated,
        }
        clarification_response: str | None = None
        if non_knowledge_response is None and resolved.resolution.outcome is TurnOutcome.CLARIFY:
            clarification_response = (
                resolved.resolution.clarification_question
                or resolved.resolution.reason
                or "I need a bit more detail to continue."
            )

        retrieval_query = resolved.retrieval.query
        followup_mode = FollowupMode.NOT_APPLICABLE
        can_exact = False
        reuse_failure: str | None = None
        used_prior_citations: list[dict[str, Any]] = []
        originating_assistant_id: str | None = None
        retrieval_started = time.perf_counter()
        if non_knowledge_response is not None or clarification_response is not None:
            status = (
                "skipped_non_knowledge_turn"
                if non_knowledge_response is not None
                else "skipped_clarification"
            )
            retrieval_result = ContextRetrievalResult(
                chunks=[],
                diagnostics={"status": status},
            )
        else:
            followup_mode = rewrite_followup_mode(
                current_content,
                resolved.resolution.outcome,
                resolved.resolution.relation,
                resolved.resolution.followup_mode,
            )
            if followup_mode is FollowupMode.NOT_APPLICABLE and any(
                item.role == "assistant" for item in bounded_history
            ):
                followup_mode = rewrite_followup_mode(
                    current_content,
                    TurnOutcome.RESOLVED,
                    TurnRelation.FOLLOW_UP,
                )
            retained_question = retained_rewrite_question(bounded_history)
            if followup_mode is FollowupMode.PRESENTATION_ONLY and retained_question is not None:
                # Evidence relevance is evaluated against the retained factual topic,
                # while generation still receives the current presentation request.
                retrieval_query = retained_question
            previous = preceding_assistant(bounded_history)
            previous_citations = (
                citation_chunks.get(previous.id, []) if previous is not None else []
            )
            used_prior_citations = (
                used_citation_items(previous.content, previous_citations)
                if previous is not None
                else []
            )
            originating_assistant_id = str(previous.id) if previous is not None else None
            seeds = rewrite_citation_ids(
                current_content,
                resolved.resolution.outcome,
                resolved.resolution.relation,
                bounded_history,
                citation_chunks,
                mode=followup_mode,
            )
            mixed_web = bool(
                previous is not None
                and used_citations_include_web(previous.content, previous_citations)
            )
            saved_scope = saved_evidence_scope(used_prior_citations)
            scope_conflict = request_scope_conflicts(
                request_filters, saved_scope
            ) or citation_scopes_conflict(used_prior_citations)
            mixed_blocks_indexed_reuse = mixed_web and (
                self._chat_config.response_mode is not ResponseMode.INDEXED_AND_WEB
            )
            provenance_ready = citations_have_exact_reuse_provenance(used_prior_citations)
            if preflight is not None and followup_mode is FollowupMode.PRESENTATION_ONLY:
                if mixed_blocks_indexed_reuse:
                    reuse_failure = "mixed_web"
                elif scope_conflict:
                    reuse_failure = "request_scope_changed"
                elif getattr(self._retrieval, "supports_exact_recall", False) is not True:
                    reuse_failure = "exact_recall_unsupported"
                elif seeds and not provenance_ready:
                    reuse_failure = "missing_provenance"
            can_exact = (
                preflight is not None
                and followup_mode is FollowupMode.PRESENTATION_ONLY
                and bool(seeds)
                and getattr(self._retrieval, "supports_exact_recall", False) is True
                and not scope_conflict
                and not mixed_blocks_indexed_reuse
                and provenance_ready
            )
            if seeds:
                self._work.counts["cited_recall_requests"] += 1
            retrieval_stage = "finding_cited_passages" if seeds else "finding_relevant_sources"
            with self._work.stage(retrieval_stage):
                retrieval_result = await retrieve_rewrite_context(
                    self._retrieval,
                    seeds=seeds,
                    mode=followup_mode,
                    prefer_exact=can_exact,
                    request={
                        "query": retrieval_query,
                        "top_k": self._retrieval_config.default_top_k,
                        "document_id": resolved.retrieval.document_id,
                        "metadata_filter": resolved.retrieval.metadata_filter or None,
                        "as_of": resolved.retrieval.as_of,
                    },
                )
        if reuse_failure:
            retrieval_result.diagnostics["presentation_reuse"] = {
                "status": "fallback",
                "reuse_failure_category": reuse_failure,
                "scope": "scoped_ranked_recall",
                "passage_count": 0,
                "originating_assistant_message_id": originating_assistant_id,
                "routing_origin": "deterministic",
                "new_coverage_review": False,
            }
        chunks = retrieval_result.chunks
        retrieval_ms = int((time.perf_counter() - retrieval_started) * 1000)
        self._work.timings["initial_retrieval"] += retrieval_ms
        missing_inputs: tuple[str, ...] = ()
        partial_answer: dict[str, Any] | None = None
        preparation_error: ProviderError | None = None
        coverage_started = time.perf_counter()
        scope_current_authority = _scope_current_authority_status(
            request,
            retrieval_result.diagnostics,
            document_id=resolved.retrieval.document_id,
        )
        query_embedder = getattr(self._retrieval, "query_embedder", None)
        grounding = (
            GroundingService(self._chat_config, embedder=query_embedder)
            if query_embedder is not None
            else self._grounding
        )
        rerank_status = str(retrieval_result.diagnostics.get("rerank_status") or "") or None
        expansion_records = list(
            retrieval_result.diagnostics.get("modifies_expansion_records") or []
        )
        context_builder = ContextBuilder(
            self._chat_config,
            evidence_approach=self._evidence_approach,
            question=retrieval_query,
        )
        presentation_reused = False
        repair_usage: ChatUsage | None = None
        presentation_only = followup_mode is FollowupMode.PRESENTATION_ONLY
        knowledge_selected: list[ContextChunk] = []
        evidence = EvidenceDecision(sufficient=False)
        authority_date = (resolved.retrieval.as_of or payload.reference_time).date()
        if can_exact:
            self._work.counts["source_version_checks"] += 1
            with self._work.stage("checking_source_versions"):
                units, reuse_diag = reconstruct_reused_evidence(
                    chunks=chunks,
                    citations=used_prior_citations,
                    expansion_records=expansion_records,
                    context_builder=context_builder,
                    current_configuration_hash=str(
                        retrieval_result.diagnostics.get("configuration_hash") or ""
                    )
                    or None,
                    current_config_snapshot_id=self._config_snapshot_id,
                    reference_date=authority_date,
                )
            origin_metadata = (
                assistant_metadata_by_id.get(originating_assistant_id, previous_assistant_metadata)
                if originating_assistant_id is not None
                else previous_assistant_metadata
            )
            inherited = _inherited_coverage_diagnostics(
                originating_message_id=originating_assistant_id,
                knowledge_repair=dict(origin_metadata.get("knowledge_repair") or {}),
                evidence_summary=dict(origin_metadata.get("evidence_summary") or {}),
                previous_inherited=dict(origin_metadata.get("inherited_coverage") or {}),
            )
            if units:
                presentation_reused = True
                knowledge_selected = list[ContextChunk](units)
                evidence = reused_evidence_decision(units)
                retrieval_result.diagnostics["presentation_reuse"] = {
                    **reuse_diag,
                    "status": "reused",
                    "scope": "current_exact_citations",
                    "passage_count": len(units),
                    "originating_assistant_message_id": originating_assistant_id,
                    "routing_origin": "deterministic",
                    "inherited_coverage": inherited,
                    "new_coverage_review": False,
                }
                retrieval_result.diagnostics["knowledge_repair"] = {
                    "status": "not_needed",
                    "reason": "presentation_only_reuses_active_cited_evidence",
                    "scope": "current_recalled_passages",
                }
                if inherited is not None:
                    retrieval_result.diagnostics["inherited_coverage"] = inherited
                    inherited_partial = inherited.get("partial_answer")
                    partial_answer = (
                        inherited_partial if isinstance(inherited_partial, dict) else None
                    )
                    missing_inputs = tuple(
                        str(item) for item in (inherited.get("missing_inputs") or [])
                    )
                    retrieval_result.diagnostics["answerable_scope"] = {
                        "complete": (
                            not inherited.get("coverage_partial")
                            and inherited.get("coverage") == "complete"
                        ),
                        "partial": bool(inherited.get("coverage_partial")),
                        "unresolved_facets": list(
                            (partial_answer or {}).get("pending")
                            or (partial_answer or {}).get("exclusions")
                            or []
                        ),
                        "missing_inputs": list(missing_inputs),
                        "supported_requirement_ids": list(
                            (partial_answer or {}).get("requirement_ids") or []
                        ),
                    }
            else:
                with self._work.stage("finding_cited_passages"):
                    fallback = await retrieve_rewrite_context(
                        self._retrieval,
                        seeds=rewrite_citation_ids(
                            current_content,
                            resolved.resolution.outcome,
                            resolved.resolution.relation,
                            bounded_history,
                            citation_chunks,
                            mode=followup_mode,
                        ),
                        mode=followup_mode,
                        prefer_exact=False,
                        request={
                            "query": retrieval_query,
                            "top_k": self._retrieval_config.default_top_k,
                            "document_id": resolved.retrieval.document_id,
                            "metadata_filter": resolved.retrieval.metadata_filter or None,
                            "as_of": resolved.retrieval.as_of,
                        },
                    )
                fallback.diagnostics["presentation_reuse"] = {
                    **reuse_diag,
                    "status": "fallback",
                    "scope": "scoped_ranked_recall",
                    "passage_count": 0,
                    "originating_assistant_message_id": originating_assistant_id,
                    "routing_origin": "deterministic",
                    "new_coverage_review": True,
                }
                retrieval_result = fallback
                chunks = retrieval_result.chunks
                expansion_records = list(
                    retrieval_result.diagnostics.get("modifies_expansion_records") or []
                )
                rerank_status = str(retrieval_result.diagnostics.get("rerank_status") or "") or None
        if not presentation_reused:
            self._work.counts["source_version_checks"] += 1
            with self._work.stage("checking_source_versions"):
                evidence, knowledge_selected = await assess_and_select_knowledge(
                    grounding=grounding,
                    context_builder=context_builder,
                    chat_config=self._chat_config,
                    question=retrieval_query,
                    chunks=chunks,
                    rerank_status=rerank_status,
                    retrieval_config=self._retrieval_config,
                    expansion_records=expansion_records,
                    reference_date=authority_date,
                )
            rewrite_recall = retrieval_result.diagnostics.get("rewrite_recall")
            if (
                presentation_only
                and isinstance(rewrite_recall, dict)
                and rewrite_recall.get("status") == "cited_passages"
                and rewrite_recall.get("missing_seed_count") == 0
            ):
                recalled = select_exact_recalled_knowledge(
                    context_builder=context_builder,
                    chunks=chunks,
                    expansion_records=expansion_records,
                    reference_date=authority_date,
                )
                if recalled:
                    knowledge_selected = recalled
                    evidence = replace(
                        evidence,
                        sufficient=True,
                        reason=None,
                        winning_chunk_id=knowledge_selected[0].chunk_id,
                    )
                    retrieval_result.diagnostics.setdefault(
                        "knowledge_repair",
                        {
                            "status": "not_needed",
                            "reason": "presentation_only_reuses_active_cited_evidence",
                            "scope": "current_recalled_passages",
                        },
                    )
            comparison_review = (
                not presentation_only
                and evidence.sufficient
                and comparison_requested(retrieval_query)
            )
            compliance_review = (
                not presentation_only
                and self._evidence_approach == "authoritative"
                and evidence.sufficient
                and compliance_overview_requested(retrieval_query)
            )
            calculation_review = (
                not presentation_only
                and evidence.sufficient
                and _requires_calculation_coverage(retrieval_query, chunks)
            )
            applicability_review = (
                not presentation_only
                and self._evidence_approach == "authoritative"
                and evidence.sufficient
                and _requires_current_rule_coverage(retrieval_query, chunks)
            )
            relevance_repair = (
                not presentation_only
                and evidence.reason is InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD
                and evidence.best_score is not None
                and evidence.best_score >= self._chat_config.minimum_reranker_evidence_score
                and rerank_status == "applied"
            )
            if (
                evidence.reason is InsufficientEvidenceReason.UNRESOLVED_AUTHORITY
                or calculation_review
                or applicability_review
                or relevance_repair
                or comparison_review
                or compliance_review
            ) and scope_current_authority is None:
                # Similarity to a worked example does not prove that its category,
                # period or complete rule schedule applies to a new calculation.
                # An unsuccessful review must not fall back to those original hits.
                if (
                    calculation_review
                    or applicability_review
                    or comparison_review
                    or compliance_review
                ):
                    evidence = replace(
                        evidence,
                        sufficient=False,
                        reason=InsufficientEvidenceReason.UNRESOLVED_AUTHORITY,
                    )
                await self._release_read_transaction()
                repair_stage = (
                    "checking_source_applicability"
                    if applicability_review
                    else "checking_missing_details"
                )
                with self._work.stage(repair_stage):
                    repaired = await repair_knowledge_evidence(
                        inputs=resolved.retrieval.model_copy(update={"query": retrieval_query}),
                        initial=retrieval_result,
                        selected=knowledge_selected,
                        retrieval=self._retrieval,
                        llm=llm,
                        grounding=grounding,
                        chat_config=self._chat_config,
                        retrieval_config=self._retrieval_config,
                        max_output_tokens=self._llm_max_tokens(),
                        release_read_transaction=self._release_read_transaction,
                        timeout_seconds=self._llm_config.evidence_review_timeout_seconds,
                        domain_instructions=self._domain_instructions,
                        initial_decision=evidence,
                        evidence_approach=self._evidence_approach,
                    )
                repair_usage = repaired.usage
                preparation_error = repaired.failure
                repair_diagnostics = dict(repaired.diagnostics)
                repair_diagnostics["trigger"] = (
                    "calculation_completeness"
                    if calculation_review
                    else "compliance_overview"
                    if compliance_review
                    else "comparison_coverage"
                    if comparison_review
                    else "current_rule_applicability"
                    if applicability_review
                    else "relevance_recovery"
                    if relevance_repair
                    else "unresolved_authority"
                )
                if not self._store_candidate_trace:
                    repair_diagnostics["branches"] = [
                        {key: value for key, value in branch.items() if key != "retrieval"}
                        for branch in repair_diagnostics.get("branches", [])
                    ]
                retrieval_result.diagnostics["knowledge_repair"] = repair_diagnostics
                if repaired.decision is not None:
                    missing_inputs = repaired.missing_inputs
                    partial_answer = repaired.partial_answer
                    evidence = repaired.decision
                    knowledge_selected = repaired.selected
                    chunks = [
                        *chunks,
                        *[
                            c
                            for c in repaired.selected
                            if c.chunk_id not in {x.chunk_id for x in chunks}
                        ],
                    ]
                if repaired.answerable_scope:
                    retrieval_result.diagnostics["answerable_scope"] = repaired.answerable_scope
        retrieval_ms = int((time.perf_counter() - retrieval_started) * 1000)
        self._work.timings["coverage_and_recovery"] += round(
            (time.perf_counter() - coverage_started) * 1000
        )
        prompt_history = _prompt_history_for_generation(
            outcome=resolved.resolution.outcome,
            relation=resolved.resolution.relation,
            bounded=bounded_history,
            full=generation_history,
        )

        mode = self._chat_config.response_mode
        web_diagnostics: dict[str, Any] = {
            "status": "not_requested",
            "fallback_used": False,
        }
        web_chunks: list[ContextChunk] = []
        web_review_usage: ChatUsage | None = None
        scoped_request = bool(resolved.retrieval.suppress_web)
        web_requested = (
            preparation_error is None
            and non_knowledge_response is None
            and clarification_response is None
            and scope_current_authority is None
            and (
                mode is ResponseMode.INDEXED_AND_WEB
                or (mode is ResponseMode.INDEXED_THEN_WEB and grounding.blocks_generation(evidence))
            )
        )
        await self._release_read_transaction()
        if web_requested and scoped_request:
            web_diagnostics = {
                "status": "suppressed_scoped_request",
                "fallback_used": False,
            }
        elif web_requested and evidence.reason is InsufficientEvidenceReason.UNRESOLVED_AUTHORITY:
            # Web snippet relevance cannot establish commencement, amendment
            # precedence or all dependencies of a governed calculation. Until
            # web evidence supports that contract, never bypass a failed review.
            web_diagnostics = {
                "status": "suppressed_unresolved_authority",
                "fallback_used": False,
            }
        elif web_requested and self._web_search is None:
            web_diagnostics = {
                "status": "provider_unavailable",
                "fallback_used": False,
                "error_code": "web_search_not_configured",
            }
        elif web_requested:
            try:
                web_search = self._web_search
                if web_search is None:  # Defensive: the branch above handles normal composition.
                    raise ProviderError(
                        "Web search provider is unavailable",
                        provider_name="web_search",
                    )
                web_query = scoped_web_query(
                    retrieval_query,
                    self._domain_instructions,
                    (resolved.retrieval.as_of or payload.reference_time).date(),
                )
                web_result = await web_search.search(
                    web_query,
                    max_results=self._web_search_config.max_results,
                )
                accepted_evidence, acceptance = _accepted_web_evidence(
                    retrieval_query,
                    web_result.evidence,
                )
                with self._work.stage("web_evidence_review"):
                    accepted_evidence, scope_review, web_review_usage = await review_web_evidence(
                        llm=llm,
                        query=retrieval_query,
                        evidence=accepted_evidence,
                        domain_instructions=self._domain_instructions,
                        reference_date=(resolved.retrieval.as_of or payload.reference_time).date(),
                    )
                web_chunks = _web_context_chunks(accepted_evidence, web_result.provider)
                discovered_source_count = (
                    len(web_result.discovered_sources)
                    or _optional_int(web_result.diagnostics.get("source_count"))
                    or len(web_result.evidence)
                )
                if discovered_source_count == 0:
                    terminal_status = "no_sources"
                elif not web_result.evidence:
                    terminal_status = "sources_found_no_extractable_evidence"
                elif web_chunks:
                    terminal_status = "evidence_accepted"
                else:
                    terminal_status = "evidence_extracted_irrelevant"
                web_diagnostics = {
                    **web_result.diagnostics,
                    "status": terminal_status,
                    "provider": web_result.provider,
                    "model": web_result.model,
                    "provider_version": web_result.provider_version,
                    "discovered_source_count": discovered_source_count,
                    "extractable_evidence_count": len(web_result.evidence),
                    "acceptance": acceptance,
                    "scope_review": scope_review,
                    "fallback_used": (mode is ResponseMode.INDEXED_THEN_WEB and bool(web_chunks)),
                }
            except ProviderError as exc:
                web_diagnostics = {
                    "status": "failed",
                    "fallback_used": False,
                    "provider": exc.provider_name,
                    "error_code": exc.code,
                    "retryable": exc.retryable,
                }

        knowledge_usable = not grounding.blocks_generation(evidence)
        indexed_policy = {
            "sufficient": evidence.sufficient,
            "blocks_generation": not knowledge_usable,
            "reason": evidence.reason.value if evidence.reason is not None else None,
            "knowledge_usable": knowledge_usable,
        }
        if non_knowledge_response is not None or clarification_response is not None:
            selected: list[ContextChunk] = []
        elif mode is ResponseMode.INDEXED_ONLY:
            selected = knowledge_selected if knowledge_usable else []
        elif mode is ResponseMode.INDEXED_THEN_WEB:
            selected = knowledge_selected if knowledge_usable else web_chunks
        else:
            selected = _balanced_evidence(
                knowledge_selected if knowledge_usable else [],
                web_chunks,
                self._context_builder,
                self._chat_config,
            )

        source_provenance = _source_provenance(selected)
        web_fallback_used = bool(
            mode is ResponseMode.INDEXED_THEN_WEB
            and not knowledge_usable
            and source_provenance is SourceProvenance.WEB
        )
        web_diagnostics["fallback_used"] = web_fallback_used

        response_language = resolve_response_language(current_content, prompt_history)
        with self._work.stage("preparing_answer"):
            messages = self._prompt_builder.build(
                template=template,
                context_chunks=selected,
                history=prompt_history,
                user_question=current_content,
                domain_instructions=self._domain_instructions,
                prompt_profile=self._prompt_profile,
                interpretation=resolved.interpretation,
                reference_date=(resolved.retrieval.as_of or payload.reference_time).date(),
                missing_inputs=missing_inputs if knowledge_usable else (),
                partial_answer=partial_answer if knowledge_usable else None,
                response_language=response_language,
                presentation_only=presentation_only,
            )
        budget = prompt_budget(
            messages,
            model=llm.model_name,
            capacity=self._llm_config.model_context_windows.get(
                llm.model_name, self._llm_config.context_window_tokens
            ),
            reserved_output=self._llm_max_tokens(),
            evidence_text=self._prompt_builder._format_context(selected),
        )
        if not budget["within_budget"]:
            # Preserve the exact admitted proof. Silently truncating it could drop a tax dependency.
            evidence = replace(
                evidence,
                sufficient=False,
                reason=InsufficientEvidenceReason.CONTEXT_SELECTION_EMPTY,
            )
        retrieval_result.diagnostics["prompt_budget"] = budget
        question_language = response_language
        notices: list[Notice] = []
        unresolved_chunks = [
            str(chunk.chunk_id)
            for chunk in knowledge_selected
            if chunk.metadata.get("authority_status") == "unresolved"
        ]
        if unresolved_chunks:
            notices.append(
                unresolved_authority_notice(language=question_language, chunk_ids=unresolved_chunks)
            )
        if scope_current_authority is not None:
            effective_modifiers = _effective_scope_modifier_records(
                retrieval_result.diagnostics.get("modifies_expansion_records") or []
            )
            notices.append(
                scope_excludes_effective_modifier_notice(
                    language=question_language,
                    modifier_records=effective_modifiers,
                )
            )
        if web_fallback_used:
            notices.append(web_evidence_used_notice(language=question_language))

        self._work.timings["preparation"] = round(
            (time.perf_counter() - preparation_started) * 1000
        )
        return _PreparedTurn(
            prompt_version=prompt_version,
            template=template,
            selected=selected,
            knowledge_selected=knowledge_selected,
            chunks=chunks,
            history=prompt_history,
            messages=messages,
            temperature=temperature,
            llm=llm,
            retrieval_ms=retrieval_ms,
            evidence=evidence,
            retrieval_diagnostics=retrieval_result.diagnostics,
            grounding=grounding,
            source_provenance=source_provenance,
            web_search_diagnostics=web_diagnostics,
            response_language=response_language,
            web_fallback_used=web_fallback_used,
            non_knowledge_response=non_knowledge_response,
            clarification_response=clarification_response,
            scope_current_authority=scope_current_authority,
            notices=tuple(notices),
            turn_resolution=diagnostics,
            resolver_usage=_combined_auxiliary_usage(
                resolved.usage or ChatUsage(None, None) if resolved.attempted else None,
                _combined_auxiliary_usage(repair_usage, web_review_usage),
            ),
            resolver_latency_ms=resolved.latency_ms,
            preparation_error=preparation_error,
            evidence_scope={
                "document_id": resolved.retrieval.document_id,
                "metadata_filter": dict(resolved.retrieval.metadata_filter or {}),
                "as_of": (
                    resolved.retrieval.as_of.isoformat() if resolved.retrieval.as_of else None
                ),
                "snapshot_origin": (
                    resolved.snapshot.origin.value if resolved.snapshot.origin is not None else None
                ),
            },
            originating_assistant_message_id=_optional_uuid_or_none(originating_assistant_id),
            inherited_coverage=(
                dict(retrieval_result.diagnostics["inherited_coverage"])
                if isinstance(retrieval_result.diagnostics.get("inherited_coverage"), dict)
                else None
            ),
            response_policy=_assemble_response_policy(
                mode=mode,
                gate_mode=self._chat_config.evidence_gate_mode,
                indexed_policy=indexed_policy,
                partial_answer=partial_answer if knowledge_usable else None,
                repair=retrieval_result.diagnostics.get("knowledge_repair"),
                answerable_scope=retrieval_result.diagnostics.get("answerable_scope"),
                web=web_diagnostics,
                web_requested=web_requested,
                scoped_request=scoped_request,
                unresolved_authority=(
                    evidence.reason is InsufficientEvidenceReason.UNRESOLVED_AUTHORITY
                ),
                scope_current_authority=scope_current_authority is not None,
            ),
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
        verification_started = time.perf_counter()
        reason_value = str(insufficient_reason) if insufficient_reason is not None else None
        if clarification_turn:
            grounding = GroundingResult(
                claims=[],
                grounded=None,
                citation_coverage=0.0,
                claims_status="not_applicable",
            )
        else:
            with self._work.stage("claim_verification"):
                grounding = await prepared.grounding.map_claims(
                    content,
                    prepared.selected,
                    user_input=user_content_for_title,
                    coverage=(
                        prepared.inherited_coverage
                        or prepared.retrieval_diagnostics.get("knowledge_repair")
                    ),
                )
            if reason_value is not None:
                grounding = type(grounding)(claims=[], grounded=False, citation_coverage=1.0)
            elif non_knowledge_turn:
                grounding = type(grounding)(claims=[], grounded=False, citation_coverage=0.0)
            # grounded=None is only valid when generation ran on admitted evidence
            # and all segments were polarity-only / non-factual.  If generation did
            # not run, keep grounded=False as before.
            if grounding.grounded is None and not generation_ran:
                grounding = type(grounding)(
                    claims=[],
                    grounded=False,
                    citation_coverage=grounding.citation_coverage,
                )
        verification_ms = round((time.perf_counter() - verification_started) * 1000)
        self._work.timings["generation"] = generation_ms
        total_ms += verification_ms
        metadata = self._build_metadata(
            retrieval_ms=prepared.retrieval_ms,
            generation_ms=generation_ms,
            total_ms=total_ms,
            retrieved_count=len(prepared.chunks),
            selected_count=len(prepared.selected),
            retrieval_diagnostics=prepared.retrieval_diagnostics,
            selected_chunks=prepared.selected,
        )
        factual_claims = [
            claim for claim in grounding.claims if claim.get("claim_kind") != "coverage_scope"
        ]
        coverage_claims = [
            claim for claim in grounding.claims if claim.get("claim_kind") == "coverage_scope"
        ]
        supported_claims = sum(claim.get("verification") == "supported" for claim in factual_claims)
        unverified_claims = sum(
            claim.get("verification") == "unverified" for claim in factual_claims
        )
        unsupported_claims = sum(
            claim.get("verification") == "unsupported" for claim in factual_claims
        )
        factual_total = len(factual_claims)
        evidence_gate = self._grounding.diagnostics(
            prepared.evidence,
            blocked_generation=reason_value is not None,
            generation_ran=generation_ran,
        )
        if grounding.claims_status:
            evidence_gate["claims_status"] = grounding.claims_status
        citations = (
            []
            if reason_value is not None or non_knowledge_turn or clarification_turn
            else self._citations_for(
                prepared.selected,
                recalled_chunks=prepared.chunks,
                prompt_version=prepared.prompt_version,
                evidence_scope=prepared.evidence_scope,
                originating_assistant_message_id=prepared.originating_assistant_message_id,
                inherited_coverage=prepared.inherited_coverage,
                knowledge_repair=prepared.retrieval_diagnostics.get("knowledge_repair"),
                expansion_records=prepared.retrieval_diagnostics.get("modifies_expansion_records"),
            )
        )
        candidate_diagnostics = evidence_gate["candidate_wise"]
        used_markers = {int(m) for m in re.findall(r"\[(\d+)\]", content)}
        cited_snapshots = [c for i, c in enumerate(citations, 1) if i in used_markers]
        cited_chunks = [
            c
            for i, c in enumerate(prepared.selected, 1)
            if i in used_markers and i <= len(citations)
        ]
        metadata["current_authority"]["cited"] = cited_authority_summary(
            cited_chunks, prepared.retrieval_diagnostics.get("modifies_expansion_records")
        )
        selected_evidence_units = [
            chunk for chunk in prepared.selected if chunk.metadata.get("evidence_unit_id")
        ]
        cited_evidence_units = [
            citation for citation in cited_snapshots if citation.get("evidence_unit_id")
        ]
        candidate_diagnostics.update(
            {
                "retrieved_count": (
                    prepared.retrieval_diagnostics.get("retrieved_candidate_count")
                    if prepared.retrieval_diagnostics.get("retrieved_candidate_count") is not None
                    else len(prepared.chunks)
                ),
                "reranked_count": (
                    prepared.retrieval_diagnostics.get("reranked_candidate_count")
                    if prepared.retrieval_diagnostics.get("reranked_candidate_count") is not None
                    else len(prepared.chunks)
                ),
                "assessed_count": len(prepared.evidence.candidate_assessments)
                or candidate_diagnostics.get("assessed_count", 0),
                # Policy/hydration/dedup removals stay in retrieval diagnostics.
                "removed_count": prepared.retrieval_diagnostics.get("post_rerank_removed_count", 0),
                "context_selected_count": len(selected_evidence_units),
                "cited_count": len(cited_evidence_units),
            }
        )
        candidate_diagnostics["alerts"]["span_hash_mismatch_count"] = sum(
            content_hash(chunk.content) != chunk.metadata.get("evidence_span_hash")
            for chunk in selected_evidence_units
        )
        evidence_funnel = _complete_evidence_funnel(
            prepared.retrieval_diagnostics.get("evidence_funnel"),
            evidence=prepared.evidence,
            retrieved_count=len(prepared.chunks),
            selected_count=len(selected_evidence_units),
            citations=cited_snapshots,
            claims=grounding.claims,
            rerank_status=prepared.retrieval_diagnostics.get("rerank_status"),
            blocked=reason_value is not None,
            generation_ran=generation_ran,
            observe=self._chat_config.evidence_gate_mode is EvidenceGateMode.OBSERVE,
            non_knowledge_turn=non_knowledge_turn,
            clarification_turn=clarification_turn,
        )
        if not self._store_candidate_trace:
            candidate_diagnostics.pop("assessments", None)
        if prepared.retrieval_diagnostics.get("rewrite_recall"):
            metadata["rewrite_recall"] = prepared.retrieval_diagnostics["rewrite_recall"]
        if prepared.retrieval_diagnostics.get("presentation_reuse"):
            metadata["presentation_reuse"] = prepared.retrieval_diagnostics["presentation_reuse"]
        if prepared.retrieval_diagnostics.get("inherited_coverage"):
            metadata["inherited_coverage"] = prepared.retrieval_diagnostics["inherited_coverage"]
        metadata.update(
            {
                "response_mode": self._chat_config.response_mode.value,
                "response_policy": prepared.response_policy,
                "source_provenance": (
                    SourceProvenance.NONE.value
                    if clarification_turn
                    else prepared.source_provenance.value
                ),
                "web_search": prepared.web_search_diagnostics,
                "non_knowledge_turn": non_knowledge_turn,
                "citation_coverage": grounding.citation_coverage,
                "unverified_claim_rate": grounding.unverified_claim_rate,
                "unsupported_claim_rate": (
                    unsupported_claims / factual_total if factual_total else 0.0
                ),
                "claim_verification_counts": {
                    "factual": factual_total,
                    "coverage_scope": len(coverage_claims),
                    "supported": supported_claims,
                    "unverified": unverified_claims,
                    "unsupported": unsupported_claims,
                },
                "best_semantic_evidence_score": prepared.evidence.winning_semantic_score,
                "evidence_gate": evidence_gate,
                "evidence_funnel": evidence_funnel,
                "scope_current_authority": prepared.scope_current_authority
                or {"status": "not_applicable"},
                "notices": [
                    n.to_dict()
                    for n in (
                        prepared.notices
                        if reason_value is None
                        else (
                            *prepared.notices,
                            insufficient_evidence_notice(
                                language=detect_language(user_content_for_title).primary_language
                                or "en"
                            ),
                        )
                    )
                ],
            }
        )
        if prepared.turn_resolution is not None:
            metadata["turn_resolution"] = prepared.turn_resolution
        metadata["lifecycle"] = self._work.snapshot()
        metadata["prompt_budget"] = {
            **prepared.retrieval_diagnostics.get("prompt_budget", {}),
            "provider_input_tokens": next(
                (c.get("input_tokens") for c in reversed(self._work.calls) if c["kind"] == "llm"),
                None,
            )
            if generation_ran
            else None,
            "provider_output_tokens": next(
                (c.get("output_tokens") for c in reversed(self._work.calls) if c["kind"] == "llm"),
                None,
            )
            if generation_ran
            else None,
        }
        metadata["effective_behavior"] = {
            "evidence_approach": self._evidence_approach,
            "translation_enabled": self._translation_enabled,
            "response_language": prepared.response_language,
            "config_snapshot_id": str(self._config_snapshot_id)
            if self._config_snapshot_id
            else None,
            "project_revision": self._config_provenance.get("project_config_revision_number"),
        }
        validated_coverage = bool(
            (prepared.retrieval_diagnostics.get("knowledge_repair") or {})
            .get("coverage", {})
            .get("quotes_validated")
        )
        presentation_reuse = prepared.retrieval_diagnostics.get("presentation_reuse")
        reused_cited_passages = (
            int(presentation_reuse.get("passage_count") or 0)
            if isinstance(presentation_reuse, dict) and presentation_reuse.get("status") == "reused"
            else 0
        )
        ordinarily_admitted_passages = (
            sum(a.passed for a in prepared.evidence.candidate_assessments)
            if prepared.evidence.candidate_assessments
            else len(prepared.evidence.admitted_units)
        )
        inherited_coverage = prepared.retrieval_diagnostics.get("inherited_coverage")
        inherited_partial = isinstance(inherited_coverage, dict) and (
            inherited_coverage.get("coverage_partial") is True
            or inherited_coverage.get("coverage") == "partial"
        )
        repair_partial = bool(
            (prepared.retrieval_diagnostics.get("knowledge_repair") or {}).get("partial_answer")
        )
        metadata["evidence_summary"] = {
            "candidates": prepared.retrieval_diagnostics.get("retrieved_candidate_count")
            or len(prepared.chunks),
            "candidate_count_scope": "initial_search_before_reranking",
            "admitted_passages": 0 if reused_cited_passages else ordinarily_admitted_passages,
            "reused_cited_passages": reused_cited_passages,
            "context_passages": len(prepared.selected),
            "cited_passages": len(cited_snapshots),
            "cited_documents": len({c.document_id for c in cited_chunks}),
            "reviewed_works": reviewed_work_count(prepared.selected),
            "coverage": "partial"
            if repair_partial or (reused_cited_passages and inherited_partial)
            else "incomplete"
            if not prepared.evidence.sufficient
            else "complete"
            if validated_coverage and not reused_cited_passages
            else "not_assessed",
            "coverage_method": (
                "validated_authoritative_dependencies"
                if self._evidence_approach == "authoritative"
                else "validated_requirements"
            )
            if validated_coverage and not reused_cited_passages
            else "current_exact_citation_recall"
            if reused_cited_passages
            else "ordinary_admission",
            "claim_verification": "verified"
            if grounding.grounded
            else "unverified"
            if grounding.grounded is False
            else "not_applicable",
            "factual_claims": factual_total,
            "coverage_scope_claims": len(coverage_claims),
            "supported_factual_claims": supported_claims,
            "unverified_factual_claims": unverified_claims,
            "unsupported_factual_claims": unsupported_claims,
            "input_provenance": {
                "unresolved_inputs": (
                    list(inherited_coverage.get("missing_inputs") or [])
                    if reused_cited_passages and isinstance(inherited_coverage, dict)
                    else (
                        (prepared.retrieval_diagnostics.get("knowledge_repair") or {})
                        .get("coverage", {})
                        .get("missing_inputs", [])
                        if validated_coverage
                        else []
                    )
                ),
                "supplied_values": "current_user_message_and_validated_conversation_context",
                "authorized_assumptions": "saved_project_domain_policy",
                "config_snapshot_id": str(self._config_snapshot_id)
                if self._config_snapshot_id
                else None,
                "verification_scope": "Source coverage does not prove personal scenario inputs "
                "or assumed membership.",
            },
        }
        persistence_started = time.perf_counter()
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
            claims=grounding.claims,
            grounded=grounding.grounded,
            insufficient_evidence_reason=reason_value,
            user_content_for_title=user_content_for_title,
        )
        self._work.timings["persistence"] += round(
            (time.perf_counter() - persistence_started) * 1000
        )
        log_kwargs: dict[str, Any] = {
            "lifecycle": self._work.snapshot(),
            "project_id": str(self._project_id),
            "conversation_id": str(conversation.id),
            "total_time_ms": total_ms,
            "retrieval_time_ms": prepared.retrieval_ms,
            "generation_time_ms": generation_ms,
            "retrieval_strategy": self._retrieval_config.strategy.value,
            "retrieval_top_k": self._retrieval_config.default_top_k,
            "retrieved_chunk_count": len(prepared.chunks),
            "provider": provider,
            "model": model,
            "streamed": streamed,
            "grounded": grounding.grounded,
            "insufficient_evidence_reason": reason_value,
            "evidence_gate_mode": evidence_gate["mode"],
            "evidence_gate_sufficient": evidence_gate["sufficient"],
            "generation_ran": generation_ran,
            "response_mode": self._chat_config.response_mode.value,
            "source_provenance": prepared.source_provenance.value,
            "web_search_status": prepared.web_search_diagnostics.get("status"),
        }
        if input_tokens_logged is not None:
            log_kwargs["input_tokens"] = input_tokens_logged
        if output_tokens_logged is not None:
            log_kwargs["output_tokens"] = output_tokens_logged
        logger.info("chat_complete", **log_kwargs)
        return assistant_message

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
        if not self._chat_config.include_citations:
            return []
        repair = knowledge_repair or {}
        inherited = inherited_coverage or {}
        coverage_raw = repair.get("coverage")
        coverage: dict[str, Any] = coverage_raw if isinstance(coverage_raw, dict) else {}
        new_review = bool(repair.get("partial_answer") or coverage.get("quotes_validated"))
        coverage_status: str | None
        coverage_partial: bool | None
        if new_review:
            coverage_status = "partial" if repair.get("partial_answer") else "complete"
            coverage_partial = bool(repair.get("partial_answer"))
            coverage_origin = None
        else:
            status_raw = inherited.get("coverage_status") or inherited.get("coverage")
            coverage_status = str(status_raw) if status_raw else None
            partial_raw = inherited.get("coverage_partial")
            coverage_partial = partial_raw if isinstance(partial_raw, bool) else None
            coverage_origin = _optional_uuid_or_none(inherited.get("coverage_origin_message_id"))
        return build_citation_snapshots(
            selected,
            config=self._chat_config,
            project_id=self._project_id,
            config_snapshot_id=self._config_snapshot_id,
            config_provenance=self._config_provenance,
            prompt_version=prompt_version,
            evidence_scope=evidence_scope,
            originating_assistant_message_id=originating_assistant_message_id,
            coverage_origin_message_id=(
                None
                if new_review or not inherited
                else (coverage_origin or originating_assistant_message_id)
            ),
            coverage_status=str(coverage_status) if coverage_status else None,
            coverage_partial=coverage_partial if isinstance(coverage_partial, bool) else None,
            expansion_records=expansion_records,
            recalled_chunks=recalled_chunks,
        )

    def _insufficient_content(self, prepared: _PreparedTurn, question: str) -> str:
        status = str(prepared.web_search_diagnostics.get("status") or "")
        bangla = detect_language(question).primary_language == "bn"
        if prepared.evidence.reason is InsufficientEvidenceReason.UNRESOLVED_AUTHORITY:
            repair = prepared.retrieval_diagnostics.get("knowledge_repair") or {}
            if repair.get("status") == "incomplete_plan" or (
                repair.get("status") == "repair_unavailable"
                and repair.get("failure_reason") == "invalid_model_response"
            ):
                return (
                    "সূত্র যাচাইয়ের ধাপটি বৈধ ফলাফল দেয়নি, তাই নির্ভরযোগ্য উত্তর তৈরি করা "
                    "যায়নি। এটি যাচাই প্রক্রিয়ার ত্রুটি; প্রয়োজনীয় আইন বা তথ্য সূত্রে নেই—"
                    "এমন সিদ্ধান্ত নয়।"
                    if bangla
                    else "The source verification step did not return a valid result, so a "
                    "reliable answer could not be generated. This is a verification failure; "
                    "it does not establish that the required law or information is absent "
                    "from the sources."
                )
            if self._evidence_approach != "authoritative" and not _requires_calculation_coverage(
                question, prepared.chunks
            ):
                return (
                    "প্রাসঙ্গিক সূত্র পাওয়া গেছে, কিন্তু এই প্রশ্নের উত্তর দেওয়ার জন্য "
                    "প্রয়োজনীয় তথ্য পুরোপুরি যাচাই করা যায়নি। আরও প্রাসঙ্গিক সূত্র দরকার।"
                    if bangla
                    else "I found related sources, but couldn't verify enough evidence to answer "
                    "this question reliably. More relevant source evidence is needed."
                )
            if not _requires_calculation_coverage(question, prepared.chunks):
                return (
                    "প্রাসঙ্গিক সূত্র পাওয়া গেছে, কিন্তু বিধানগুলোর প্রযোজ্য সময়কাল, শর্ত বা "
                    "সংশোধনের প্রভাব নিশ্চিত করা যায়নি। নির্ভরযোগ্য উত্তর দিতে প্রযোজ্য "
                    "বিধান ও সংশোধনের নির্দিষ্ট প্রমাণ দরকার।"
                    if bangla
                    else "Relevant sources were found, but their applicable period, conditions or "
                    "amendment effect could not be established. The applicable provisions and "
                    "amendment evidence are needed to answer this question reliably."
                )
            if bangla:
                return (
                    "প্রাসঙ্গিক সূত্র পাওয়া গেছে, কিন্তু নিয়মগুলোর প্রযোজ্য সময়কাল, শর্ত বা "
                    "সংশোধনের প্রভাব নিশ্চিত করা যায়নি। তাই এগুলো দিয়ে চূড়ান্ত হিসাব করা "
                    "নির্ভরযোগ্য হবে না। প্রযোজ্য বিধান ও সংশোধনের নির্দিষ্ট প্রমাণ দরকার।"
                )
            return (
                "Relevant sources were found, but their applicable period, conditions or "
                "amendment effect could not be established. A final calculation using those "
                "rules would be unreliable. The applicable provisions and amendment evidence "
                "are still needed."
            )
        if status in {"failed", "provider_unavailable"}:
            if bangla:
                return "উপলভ্য knowledge base-এ যথেষ্ট তথ্য পাইনি, এবং web search এখন সাময়িকভাবে অনুপলভ্য।"
            return (
                "I couldn\u2019t find enough information in the available knowledge base, and web "
                "search is temporarily unavailable."
            )
        if status in {
            "no_sources",
            "sources_found_no_extractable_evidence",
            "evidence_extracted_irrelevant",
        }:
            if bangla:
                return (
                    "উপলভ্য knowledge base বা সাম্প্রতিক web সূত্রে যথেষ্ট তথ্য পাইনি, তাই "
                    "আত্মবিশ্বাসের সঙ্গে উত্তর দিতে পারছি না।"
                )
            return (
                "I couldn\u2019t find enough information in the available knowledge base "
                "or current "
                "web sources to answer that confidently."
            )
        if bangla:
            return "উপলভ্য knowledge base-এ আত্মবিশ্বাসের সঙ্গে উত্তর দেওয়ার মতো যথেষ্ট তথ্য পাইনি।"
        return self._chat_config.insufficient_evidence_message

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
        if exc.context.get("reason") == "evidence_review_timeout":
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
        return {
            "retrieval_time_ms": retrieval_ms,
            "generation_time_ms": generation_ms,
            "total_time_ms": total_ms,
            "retrieval_strategy": self._retrieval_config.strategy.value,
            "retrieval_top_k": self._retrieval_config.default_top_k,
            "retrieved_chunk_count": retrieved_count,
            "selected_chunk_count": selected_count,
            "retrieval_trace": {
                "candidates": (
                    retrieval_diagnostics.get("candidate_trace", [])
                    if self._store_candidate_trace
                    else []
                ),
                "retrieval_selected": (
                    retrieval_diagnostics.get("selected_trace", [])
                    if self._store_candidate_trace
                    else []
                ),
                "context_selected": (
                    [
                        _context_trace_item(index, chunk)
                        for index, chunk in enumerate(selected_chunks, start=1)
                    ]
                    if self._store_candidate_trace
                    else []
                ),
                "suppression": {
                    "input_count": retrieval_diagnostics.get(
                        "duplicate_suppression_input_count", 0
                    ),
                    "removed_count": retrieval_diagnostics.get(
                        "duplicate_suppression_removed_count", 0
                    ),
                    "reasons": retrieval_diagnostics.get("duplicate_suppression_reasons", {}),
                    "diversity_deferred_reasons": retrieval_diagnostics.get(
                        "diversity_deferred_reasons", {}
                    ),
                    "diversity_backfilled_count": retrieval_diagnostics.get(
                        "diversity_backfilled_count", 0
                    ),
                },
                "rerank": {
                    "status": retrieval_diagnostics.get("rerank_status"),
                    "failure_reason": retrieval_diagnostics.get("rerank_failure_reason"),
                    "provider": retrieval_diagnostics.get("reranker_provider"),
                    "model": retrieval_diagnostics.get("reranker_model"),
                    "version": retrieval_diagnostics.get("reranker_version"),
                    "score_scale": retrieval_diagnostics.get("reranker_score_scale"),
                    "latency_ms": retrieval_diagnostics.get("reranker_latency_ms"),
                    "skipped_reason": (
                        retrieval_diagnostics.get("rerank_status")
                        if retrieval_diagnostics.get("rerank_status")
                        in {"skipped_same_language", "unavailable", "disabled", "passthrough"}
                        else None
                    ),
                },
                "rerank_status": retrieval_diagnostics.get("rerank_status"),
                "reranker_score_scale": retrieval_diagnostics.get("reranker_score_scale"),
                "translation": {
                    "status": retrieval_diagnostics.get("translation_status"),
                    "failure_reason": retrieval_diagnostics.get("translation_failure_reason"),
                    "attempts": retrieval_diagnostics.get("translation_attempts"),
                    "validation_reasons": retrieval_diagnostics.get(
                        "translation_validation_reasons"
                    )
                    or [],
                    "finish_reason": retrieval_diagnostics.get("translation_finish_reason"),
                    "skipped_reason": retrieval_diagnostics.get("skipped_reason"),
                    "source_language": retrieval_diagnostics.get("translation_source_language"),
                    "target_language": retrieval_diagnostics.get("translation_target_language"),
                    "query_language_profile": retrieval_diagnostics.get("query_language_profile"),
                    "romanized_or_codeswitched": retrieval_diagnostics.get(
                        "romanized_or_codeswitched", False
                    ),
                    "translated_query": retrieval_diagnostics.get("translated_query"),
                    "provider": retrieval_diagnostics.get("translation_provider"),
                    "model": retrieval_diagnostics.get("translation_model"),
                    "prompt_version": retrieval_diagnostics.get("translation_prompt_version"),
                    "latency_ms": retrieval_diagnostics.get("translation_latency_ms"),
                    "usage": retrieval_diagnostics.get("translation_usage") or {},
                },
                "query_variants": retrieval_diagnostics.get("query_variants") or [],
                "executed_branches": retrieval_diagnostics.get("executed_branches") or [],
                "skipped_branches": retrieval_diagnostics.get("skipped_branches") or [],
                "branch_candidate_counts": retrieval_diagnostics.get("branch_candidate_counts")
                or {},
            },
            "index_build_id": retrieval_diagnostics.get("index_build_id"),
            "embedding_set_version": retrieval_diagnostics.get("embedding_set_version"),
            "embedding": {
                "identity_status": retrieval_diagnostics.get("embedding_identity_status"),
                "provider": retrieval_diagnostics.get("embedding_provider"),
                "model": retrieval_diagnostics.get("embedding_model"),
                "dimensions": retrieval_diagnostics.get("embedding_dimensions"),
                "set_version": retrieval_diagnostics.get("embedding_set_version"),
            },
            "source_metadata_generation": retrieval_diagnostics.get("source_metadata_generation"),
            "knowledge_repair": retrieval_diagnostics.get(
                "knowledge_repair", {"status": "not_needed"}
            ),
            "source_policy": {
                "configured_mode": retrieval_diagnostics.get("source_policy_configured_mode"),
                "effective_mode": retrieval_diagnostics.get("source_policy_effective_mode"),
                "deployment_cap": retrieval_diagnostics.get("source_policy_deployment_cap"),
                "status": retrieval_diagnostics.get("source_policy_status"),
                "exclusion_reasons": retrieval_diagnostics.get(
                    "source_policy_exclusion_reasons", {}
                ),
                "consolidation_reasons": retrieval_diagnostics.get(
                    "source_policy_consolidation_reasons", {}
                ),
            },
            "current_authority": {
                "status": retrieval_diagnostics.get("modifies_expansion_status"),
                "scope": "retrieval_expansion",
                "depth": retrieval_diagnostics.get("modifies_expansion_depth"),
                "records": retrieval_diagnostics.get("modifies_expansion_records") or [],
                "exclusion_reasons": retrieval_diagnostics.get(
                    "modifies_expansion_exclusion_reasons", {}
                ),
                "authority_scope_status": retrieval_diagnostics.get(
                    "modifies_authority_scope_status", "not_applicable"
                ),
                "authority_unscoped_count": retrieval_diagnostics.get(
                    "modifies_authority_unscoped_count", 0
                ),
                "related_source_count": retrieval_diagnostics.get("related_source_count", 0),
                "relationship_candidate_count": retrieval_diagnostics.get(
                    "relationship_candidate_count", 0
                ),
                "reranked_candidate_count": retrieval_diagnostics.get(
                    "reranked_candidate_count", 0
                ),
                "post_rerank_removed_count": retrieval_diagnostics.get(
                    "post_rerank_removed_count", 0
                ),
                "post_rerank_removal_reasons": retrieval_diagnostics.get(
                    "post_rerank_removal_reasons", {}
                ),
                "post_rerank_unfilled_slots": retrieval_diagnostics.get(
                    "post_rerank_unfilled_slots", 0
                ),
                "cited": cited_authority_summary(
                    selected_chunks,
                    retrieval_diagnostics.get("modifies_expansion_records"),
                ),
            },
            "retrieval_reference_date": retrieval_diagnostics.get("reference_date"),
            "retrieval_as_of": retrieval_diagnostics.get("as_of"),
            "retrieval_configuration_hash": retrieval_diagnostics.get("configuration_hash"),
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


def _accepted_web_evidence(
    query: str,
    evidence: list[WebSearchEvidence],
) -> tuple[list[WebSearchEvidence], dict[str, int]]:
    """Apply a small fail-closed admission check to provider-normalized web evidence."""
    query_tokens = {
        token for token in tokenize(query, for_query=True) if token not in _WEB_QUERY_STOPWORDS
    }
    query_language = detect_language(query).primary_language
    accepted: list[WebSearchEvidence] = []
    rejected_invalid = 0
    rejected_irrelevant = 0
    for item in evidence:
        valid_source = (
            item.citation_verified
            and item.url.startswith(("http://", "https://"))
            and bool(item.title.strip())
            and bool(item.content.strip())
        )
        if not valid_source:
            rejected_invalid += 1
            continue
        evidence_language = detect_language(f"{item.title}\n{item.content}").primary_language
        evidence_tokens = set(tokenize(f"{item.title}\n{item.content}", for_query=True))
        same_language = query_language is not None and query_language == evidence_language
        # A same-language result must share at least one meaningful query token. For a
        # cross-language source we retain the provider's cited source association instead of
        # pretending a lexical-only check can reliably assess translation relevance.
        if same_language and query_tokens and not (query_tokens & evidence_tokens):
            rejected_irrelevant += 1
            continue
        accepted.append(item)
    return accepted, {
        "accepted_count": len(accepted),
        "rejected_invalid_count": rejected_invalid,
        "rejected_irrelevant_count": rejected_irrelevant,
    }


def _web_context_chunks(
    evidence: list[WebSearchEvidence],
    provider: str,
) -> list[ContextChunk]:
    chunks: list[ContextChunk] = []
    for index, item in enumerate(evidence):
        document_id = uuid.uuid5(uuid.NAMESPACE_URL, item.url)
        chunk_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{item.url}#{item.evidence_id}")
        chunks.append(
            ContextChunk(
                chunk_id=chunk_id,
                document_id=document_id,
                chunk_index=index,
                content=item.content,
                score=0.0,
                filename=item.title or item.url,
                chunk_hash=item.evidence_id,
                char_start=0,
                char_end=len(item.content),
                metadata={
                    "source_kind": CitationSourceKind.WEB.value,
                    "source_title": item.title,
                    "web_title": item.title,
                    "web_url": item.url,
                    "web_retrieved_at": item.retrieved_at.isoformat(),
                    "web_provider": provider,
                },
            )
        )
    return chunks


def _context_trace_item(index: int, chunk: ContextChunk) -> dict[str, Any]:
    """Persist source-appropriate selection diagnostics without leaking synthetic web IDs."""
    if chunk.metadata.get("source_kind") == CitationSourceKind.WEB.value:
        return {
            "rank": index,
            "source_kind": CitationSourceKind.WEB.value,
            "web_url": chunk.metadata.get("web_url"),
            "web_title": chunk.metadata.get("web_title"),
            "web_provider": chunk.metadata.get("web_provider"),
            "web_retrieved_at": chunk.metadata.get("web_retrieved_at"),
        }
    return {
        "rank": index,
        "source_kind": CitationSourceKind.KNOWLEDGE.value,
        "chunk_id": str(chunk.chunk_id),
        "document_id": str(chunk.document_id),
        "chunk_index": chunk.chunk_index,
        "score": chunk.score,
        "rank_score": chunk.rank_score,
        "semantic_score": chunk.semantic_score,
        "rerank_relevance_score": chunk.rerank_relevance_score,
        "passage_semantic_score": chunk.passage_semantic_score,
        "passage_score_method": chunk.passage_score_method,
        "evidence_unit_id": chunk.metadata.get("evidence_unit_id"),
        "evidence_span_hash": chunk.metadata.get("evidence_span_hash"),
        "evidence_chunk_char_start": chunk.metadata.get("evidence_chunk_char_start"),
        "evidence_chunk_char_end": chunk.metadata.get("evidence_chunk_char_end"),
        "evidence_span_derivation": chunk.metadata.get("evidence_span_derivation"),
        "evidence_query_variant_id": chunk.metadata.get("evidence_query_variant_id"),
        "authority_redaction": chunk.metadata.get("authority_redaction"),
        "authority_redacted_provisions": chunk.metadata.get("authority_redacted_provisions") or [],
    }


def _complete_evidence_funnel(
    retrieval_funnel: object,
    *,
    evidence: EvidenceDecision,
    retrieved_count: int,
    selected_count: int,
    citations: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    rerank_status: object,
    blocked: bool,
    generation_ran: bool,
    observe: bool,
    non_knowledge_turn: bool,
    clarification_turn: bool = False,
) -> dict[str, Any]:
    """Complete the compact retrieval funnel with admission and answer stages."""
    funnel = dict(retrieval_funnel) if isinstance(retrieval_funnel, dict) else {}
    for stage in ("fused", "reranked", "policy_survived", "hydrated", "deduped"):
        funnel.setdefault(stage, 0)
    losses = dict(funnel.get("loss_reasons") or {})
    rejected: dict[str, int] = {}
    for assessment in evidence.candidate_assessments:
        if assessment.passed:
            continue
        reason = assessment.terminal_reason or "not_admitted"
        rejected[reason] = rejected.get(reason, 0) + 1
    if rejected:
        losses["admitted"] = rejected
    assessed_count = len(evidence.candidate_assessments) or retrieved_count
    admitted_count = len(evidence.admitted_units)
    if not rejected and assessed_count > admitted_count:
        reason = evidence.reason.value if evidence.reason is not None else "not_admitted"
        losses["admitted"] = {reason: assessed_count - admitted_count}
    if admitted_count > selected_count:
        losses["context_selected"] = {
            "authority_or_context_budget": admitted_count - selected_count
        }
    cited_unit_ids = {
        str(citation.get("evidence_unit_id"))
        for citation in citations
        if citation.get("evidence_unit_id")
    }
    if selected_count > len(cited_unit_ids):
        losses["cited"] = {"not_cited": selected_count - len(cited_unit_ids)}
    supported_claims = sum(bool(claim.get("grounded")) for claim in claims)
    funnel.update(
        {
            "assessed": assessed_count,
            "admitted": admitted_count,
            "context_selected": selected_count,
            "cited": len(cited_unit_ids),
            "supported_claims": supported_claims,
            "rerank_status": str(rerank_status or funnel.get("rerank_status") or "unknown"),
            "grounding_path": evidence.grounding_path,
            "loss_reasons": losses,
            "would_have_blocked": bool(observe and not evidence.sufficient),
            "observe_context": evidence.observe_context,
            "outcome": (
                "clarification"
                if clarification_turn
                else "non_knowledge"
                if non_knowledge_turn
                else "refused"
                if blocked
                else "answered"
                if generation_ran
                else "not_generated"
            ),
        }
    )
    return funnel


def _source_provenance(chunks: list[ContextChunk]) -> SourceProvenance:
    has_web = any(
        chunk.metadata.get("source_kind") == CitationSourceKind.WEB.value for chunk in chunks
    )
    has_knowledge = any(
        chunk.metadata.get("source_kind") != CitationSourceKind.WEB.value for chunk in chunks
    )
    if has_web and has_knowledge:
        return SourceProvenance.KNOWLEDGE_AND_WEB
    if has_web:
        return SourceProvenance.WEB
    if has_knowledge:
        return SourceProvenance.KNOWLEDGE
    return SourceProvenance.NONE


def _balanced_evidence(
    knowledge: list[ContextChunk],
    web: list[ContextChunk],
    builder: ContextBuilder,
    config: ChatConfig,
) -> list[ContextChunk]:
    """Interleave and bound both source families so one cannot crowd out the other."""
    if not knowledge:
        return builder.select(web)
    if not web:
        return builder.select(knowledge)
    ordered: list[ContextChunk] = []
    for index in range(max(len(knowledge), len(web))):
        if index < len(knowledge):
            ordered.append(knowledge[index])
        if index < len(web):
            ordered.append(web[index])
    per_item_budget = max(1, config.context_char_budget // config.max_context_chunks)
    bounded = [
        replace(chunk, content=chunk.content[:per_item_budget])
        if not isinstance(chunk, EvidenceUnit) and len(chunk.content) > per_item_budget
        else chunk
        for chunk in ordered
    ]
    return builder.select(bounded)


def _non_knowledge_response(content: str) -> str | None:
    normalized = " ".join(content.casefold().strip(" .,!?:;\u0964\u0965").split())
    english = {
        "hi": "Hello! How can I help with your knowledge base?",
        "hello": "Hello! How can I help with your knowledge base?",
        "hey": "Hello! How can I help with your knowledge base?",
        "thanks": "You\u2019re welcome.",
        "thank you": "You\u2019re welcome.",
        "got it": "Glad that helped.",
        "okay": "Okay.",
        "ok": "Okay.",
    }
    bangla = {
        "হাই": "হ্যালো! উপলভ্য knowledge base থেকে আমি কীভাবে সাহায্য করতে পারি?",
        "হ্যালো": "হ্যালো! উপলভ্য knowledge base থেকে আমি কীভাবে সাহায্য করতে পারি?",
        "ধন্যবাদ": "স্বাগতম।",
        "বুঝতে পেরেছি": "ভালো লাগল।",
    }
    return english.get(normalized) or bangla.get(normalized)


_EFFECTIVE_MODIFIER_OUTCOMES = frozenset(
    {
        "expanded",
        "already_in_recall",
        "candidate_cap_exceeded",
        "source_cap_exceeded",
    }
)


def _effective_scope_modifier_records(records: object) -> list[dict[str, Any]]:
    """Keep only MODIFIES records that were eligible for this as-of window."""
    if not isinstance(records, list):
        return []
    return [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("relationship_type") == "modifies"
        and record.get("modifier_effective_from")
        and record.get("outcome") in _EFFECTIVE_MODIFIER_OUTCOMES
    ]


def _scope_current_authority_status(
    request: MessageSendRequest,
    diagnostics: dict[str, Any],
    *,
    document_id: uuid.UUID | None = None,
) -> dict[str, Any] | None:
    """Detect when a hard-scope request excludes its effective modifier.

    This is now language-neutral: the English 'current' token guard has been
    removed.  Any document-scoped request where MODIFIES expansion was suppressed
    and at least one effective modifier record exists triggers a structured notice
    (not a refusal); generation proceeds from admitted scoped evidence.
    """
    scoped_document = document_id if document_id is not None else request.document_id
    if scoped_document is None:
        return None
    if diagnostics.get("modifies_expansion_status") != "suppressed_document_scope":
        return None
    records = diagnostics.get("modifies_expansion_records") or []
    effective_modifiers = _effective_scope_modifier_records(records)
    if not effective_modifiers:
        return None
    return {
        "status": "effective_modifier_excluded_by_scope",
        "reason": "effective_modifier_excluded_by_document_scope",
        "scoped_evidence_available": True,
        "excluded_effective_modifier_count": len(effective_modifiers),
    }


# _scope_limited_current_authority_content removed in Phase 3.
# Hard-scope + effective-modifier excluded now answers from admitted scoped
# evidence with a structured notice rather than refusing generation.


def _combined_auxiliary_usage(
    first: ChatUsage | None, second: ChatUsage | None
) -> ChatUsage | None:
    if second is None:
        return first
    if first is None:
        return second
    return ChatUsage(
        input_tokens=None
        if first.input_tokens is None or second.input_tokens is None
        else first.input_tokens + second.input_tokens,
        output_tokens=None
        if first.output_tokens is None or second.output_tokens is None
        else first.output_tokens + second.output_tokens,
    )


_PROGRESS_PRIORITY: tuple[tuple[str, tuple[str, str]], ...] = (
    ("claim_verification", ("verifying_citations", "Verifying citations")),
    ("answer_generation", ("generating_answer", "Preparing your answer")),
    ("preparing_answer", ("generating_answer", "Preparing your answer")),
    ("web_evidence_review", ("checking_source_applicability", "Checking source applicability")),
    (
        "checking_source_applicability",
        ("checking_source_applicability", "Checking source applicability"),
    ),
    ("scenario_input_review", ("checking_missing_details", "Checking missing details")),
    ("coverage_review", ("checking_missing_details", "Checking missing details")),
    ("selector_retry", ("checking_missing_details", "Checking missing details")),
    ("structured_response_retry", ("checking_missing_details", "Checking missing details")),
    ("recovery_planning", ("checking_missing_details", "Checking missing details")),
    ("checking_missing_details", ("checking_missing_details", "Checking missing details")),
    ("checking_source_versions", ("checking_source_versions", "Checking source versions")),
    ("finding_cited_passages", ("finding_cited_passages", "Finding cited passages")),
    ("finding_relevant_sources", ("finding_relevant_sources", "Finding relevant sources")),
    ("ranked_retrieval", ("finding_relevant_sources", "Finding relevant sources")),
    ("reranking", ("finding_relevant_sources", "Finding relevant sources")),
    ("retrieval", ("finding_relevant_sources", "Finding relevant sources")),
    ("turn_resolution", ("understanding_request", "Understanding request")),
)


def _preparation_progress(work: RequestWork) -> tuple[str, str]:
    """Describe long preparation work from active phases, not LLM/reranker counts."""
    active = work.active_purposes()
    for purpose, label in _PROGRESS_PRIORITY:
        if purpose in active:
            return label
    if work.counts.get("cited_recall_requests"):
        return "finding_cited_passages", "Finding cited passages"
    return "understanding_request", "Understanding request"


def _combine_token_counts(
    resolver: ChatUsage | None,
    generation_input: int | None,
    generation_output: int | None,
) -> tuple[int | None, int | None]:
    if resolver is None:
        return generation_input, generation_output
    return (
        _sum_known_tokens(resolver.input_tokens, generation_input),
        _sum_known_tokens(resolver.output_tokens, generation_output),
    )


def _sum_known_tokens(left: int | None, right: int | None) -> int | None:
    if left is None or right is None:
        return None
    return left + right


def _is_resolver_history_row(message: Message) -> bool:
    if message.role is MessageRole.SYSTEM:
        return False
    if message.role is MessageRole.ASSISTANT and message.finish_reason == "error":
        return False
    return message.role in {MessageRole.USER, MessageRole.ASSISTANT}


def _history_message_from_orm(message: Message) -> HistoryMessage:
    if message.role is MessageRole.USER:
        return HistoryMessage(
            id=message.id,
            role="user",
            content=message.content,
            citations=_citation_identities_from_message(message),
        )
    return HistoryMessage(
        id=message.id,
        role="assistant",
        content=message.content,
        citations=_citation_identities_from_message(message),
    )


def _citation_identities_from_message(message: Message) -> list[CitationIdentity]:
    identities: list[CitationIdentity] = []
    for item in used_citation_items(message.content, message.citations or []):
        identities.append(
            CitationIdentity(
                message_id=message.id,
                document_id=_optional_uuid_or_none(item.get("document_id")),
                filename=_optional_str(item.get("filename")),
                source_title=_optional_str(item.get("source_title") or item.get("web_title")),
                source_published_date=_optional_date(item.get("source_published_date")),
                source_effective_from=_optional_date(item.get("source_effective_from")),
                source_effective_to=_optional_date(item.get("source_effective_to")),
            )
        )
    return identities


def _prompt_history_for_generation(
    *,
    outcome: TurnOutcome,
    relation: TurnRelation,
    bounded: list[HistoryMessage],
    full: list[PromptHistoryMessage],
) -> list[PromptHistoryMessage]:
    if outcome is TurnOutcome.FALLBACK:
        return full
    if (
        outcome is TurnOutcome.CLARIFY
        or outcome is TurnOutcome.STANDALONE
        or relation is TurnRelation.TOPIC_CHANGE
    ):
        return []
    return [
        PromptHistoryMessage(
            role=MessageRole.USER if item.role == "user" else MessageRole.ASSISTANT,
            content=item.content,
        )
        for item in bounded
    ]


def _compact_resolution_summary(metadata: dict[str, Any]) -> dict[str, Any] | None:
    recorded = metadata.get("turn_resolution")
    if not isinstance(recorded, dict) or not recorded:
        return None
    keys = (
        "version",
        "outcome",
        "relation",
        "followup_mode",
        "new_factual_facets",
        "reason",
        "effective_question",
        "query_changed",
        "filter_changed",
        "failure_code",
        "failure_field",
        "bypass_reason",
        "latency_ms",
        "routing_origin",
    )
    return {key: recorded[key] for key in keys if key in recorded}


def _assemble_response_policy(
    *,
    mode: ResponseMode,
    gate_mode: EvidenceGateMode,
    indexed_policy: dict[str, Any],
    partial_answer: dict[str, Any] | None,
    repair: object,
    answerable_scope: object,
    web: dict[str, Any],
    web_requested: bool,
    scoped_request: bool,
    unresolved_authority: bool,
    scope_current_authority: bool,
) -> dict[str, Any]:
    """Separate indexed routing policy from validated answerable scope."""
    repair_payload = repair if isinstance(repair, dict) else {}
    coverage_payload = repair_payload.get("coverage")
    coverage = coverage_payload if isinstance(coverage_payload, dict) else {}
    scope = answerable_scope if isinstance(answerable_scope, dict) else {}
    complete = bool(scope.get("complete") or coverage.get("full_coverage_validated"))
    partial = bool(partial_answer) or bool(
        scope.get("partial") or coverage.get("partial_scope_validated")
    )
    unresolved = list(
        scope.get("unresolved_facets")
        or (partial_answer or {}).get("pending")
        or coverage.get("missing")
        or []
    )
    return {
        "response_mode": mode.value,
        "gate_mode": gate_mode.value,
        "indexed_policy": dict(indexed_policy),
        "answerable_scope": {
            "complete": complete,
            "partial": partial and not complete,
            "unresolved_facets": [] if complete else unresolved,
            "missing_inputs": list(
                scope.get("missing_inputs") or coverage.get("missing_inputs") or []
            ),
            "supported_requirement_ids": list(
                scope.get("supported_requirement_ids")
                or (partial_answer or {}).get("requirement_ids")
                or []
            ),
        },
        "web": {
            "requested": web_requested,
            "status": web.get("status"),
            "fallback_used": bool(web.get("fallback_used")),
        },
        "scoped_request": scoped_request,
        "unresolved_authority": unresolved_authority,
        "scope_excludes_effective_modifier": scope_current_authority,
    }


def _inherited_coverage_diagnostics(
    *,
    originating_message_id: str | None,
    knowledge_repair: dict[str, Any],
    evidence_summary: dict[str, Any],
    previous_inherited: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Copy prior validated partial-scope diagnostics without treating them as a new review."""
    if not originating_message_id:
        return None
    prior = previous_inherited if isinstance(previous_inherited, dict) else {}
    coverage_payload = knowledge_repair.get("coverage")
    new_review = bool(
        knowledge_repair.get("partial_answer")
        or (isinstance(coverage_payload, dict) and coverage_payload.get("quotes_validated"))
    )
    if prior and not new_review:
        envelope = dict(prior)
        envelope["originating_assistant_message_id"] = originating_message_id
        envelope["new_review"] = False
        if not envelope.get("coverage_origin_message_id"):
            envelope["coverage_origin_message_id"] = originating_message_id
        return envelope
    coverage = evidence_summary.get("coverage")
    partial_answer = knowledge_repair.get("partial_answer")
    missing_inputs = knowledge_repair.get("missing_inputs")
    if missing_inputs is None:
        provenance = evidence_summary.get("input_provenance")
        missing_inputs = (
            provenance.get("unresolved_inputs") if isinstance(provenance, dict) else None
        )
    if coverage is None and partial_answer is None and not missing_inputs:
        return None
    origin = str(prior.get("coverage_origin_message_id") or originating_message_id)
    return {
        "originating_assistant_message_id": originating_message_id,
        "coverage_origin_message_id": origin,
        "coverage": coverage,
        "coverage_status": coverage,
        "coverage_partial": coverage == "partial" or bool(partial_answer),
        "partial_answer": partial_answer,
        "missing_inputs": missing_inputs or [],
        "coverage_method": evidence_summary.get("coverage_method"),
        "new_review": False,
    }


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _optional_uuid_or_none(value: object) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _optional_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _optional_uuid(value: object) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value is not None else None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TypeError("Execution measurements must be integer-compatible values")
    return int(value)


def _requires_calculation_coverage(question: str, chunks: list[ContextChunk]) -> bool:
    governed = any(
        c.metadata.get("source_role") in {"primary", "supporting"}
        and c.metadata.get("source_lifecycle_status") in {"active", "retired"}
        for c in chunks
    )
    return governed and bool(
        re.search(
            r"\b(?:calculat(?:e|ed|ing|ion|ions)|recalculat(?:e|ion)|"
            r"comput(?:e|ing|ation)|breakdown)\b|\bhow much\b|হিসাব|হিসেব|গণনা|পরিগণনা",
            question,
            re.IGNORECASE,
        )
    )


def _requires_current_rule_coverage(question: str, chunks: list[ContextChunk]) -> bool:
    """Current governed facts need scope proof even when similarity admission passes.

    Include reference sources: a highly similar proposal/company rule must not
    bypass applicability review merely because no governing source was selected.
    This also runs when conversation interpretation falls back to the raw question.
    """
    governed = any(
        c.metadata.get("source_role") in {"primary", "supporting", "reference"}
        and c.metadata.get("source_lifecycle_status") in {"active", "retired"}
        for c in chunks
    )
    return governed and bool(
        re.search(r"\b(?:current|currently|latest|today|now)\b|বর্তমান|সর্বশেষ|এখন", question, re.I)
    )
