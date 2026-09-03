"""RAG chat orchestration with split transaction boundaries."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
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
from app.modules.conversations.citation_snapshots import build_citation_snapshots
from app.modules.conversations.context_builder import ContextBuilder
from app.modules.conversations.grounded_context import assess_and_select_knowledge
from app.modules.conversations.grounding_service import EvidenceDecision, GroundingService
from app.modules.conversations.notices import (
    Notice,
    insufficient_evidence_notice,
    scope_excludes_effective_modifier_notice,
    web_evidence_used_notice,
)
from app.modules.conversations.ports import (
    ContextChunk,
    ContextRetrievalResult,
    EvidenceUnit,
    RetrievalPort,
)
from app.modules.conversations.prompt_builder import PromptBuilder, PromptHistoryMessage
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
    MessageResponse,
    MessageSendRequest,
    SourceProvenance,
)
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
from app.platform.providers.errors import ProviderError

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
    web_fallback_used: bool = False
    non_knowledge_response: str | None = None
    scope_current_authority: dict[str, Any] | None = None
    notices: tuple[Notice, ...] = ()


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
        self._context_builder = ContextBuilder(chat_config)
        self._prompt_builder = PromptBuilder()
        self._grounding = GroundingService(
            chat_config,
            embedder=embedder,
        )

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

        if not prepared.selected:
            assistant_message = await self._persist_assistant_turn(
                conversation=conversation,
                prepared=prepared,
                content=self._insufficient_content(prepared, request.content),
                finish_reason="insufficient_evidence",
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
        assistant_message = await self._persist_assistant_turn(
            conversation=conversation,
            prepared=prepared,
            content=content,
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

        if not prepared.selected:
            content = self._insufficient_content(prepared, request.content)
            yield content
            assistant_message = await self._persist_assistant_turn(
                conversation=conversation,
                prepared=prepared,
                content=content,
                finish_reason="insufficient_evidence",
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
                insufficient_reason=prepared.evidence.reason,
                generation_ran=False,
            )
            yield self._done_event(assistant_message, conversation)
            return

        generation_started = time.perf_counter()
        content_parts: list[str] = []
        finish_reason: str | None = None
        final_usage: ChatUsage | None = None

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
                if chunk.usage is not None:
                    final_usage = chunk.usage
        except ProviderError as exc:
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
            input_tokens=final_usage.input_tokens if final_usage is not None else None,
            output_tokens=final_usage.output_tokens if final_usage is not None else None,
            provider=prepared.llm.provider_name,
            model=prepared.llm.model_name,
            generation_ms=generation_ms,
            total_ms=total_ms,
            user_content_for_title=request.content,
            streamed=True,
            input_tokens_logged=final_usage.input_tokens if final_usage is not None else None,
            output_tokens_logged=final_usage.output_tokens if final_usage is not None else None,
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
        history_limit = self._chat_config.max_history_messages
        fetch_limit = history_limit + 1 if history_limit > 0 else 1
        history = await self._message_repository.list_recent_for_conversation(
            conversation_id,
            limit=fetch_limit,
        )
        history = [message for message in history if message.id != user_message.id]
        history = history[-history_limit:] if history_limit > 0 else []
        non_knowledge_response = _non_knowledge_response(request.content)
        retrieval_query = request.content

        retrieval_started = time.perf_counter()
        if non_knowledge_response is None:
            retrieval_result = await self._retrieval.retrieve(
                query=retrieval_query,
                top_k=self._retrieval_config.default_top_k,
                document_id=request.document_id,
                metadata_filter=request.metadata_filter or None,
                as_of=request.as_of,
            )
        else:
            retrieval_result = ContextRetrievalResult(
                chunks=[],
                diagnostics={"status": "skipped_non_knowledge_turn"},
            )
        chunks = retrieval_result.chunks
        retrieval_ms = int((time.perf_counter() - retrieval_started) * 1000)
        scope_current_authority = _scope_current_authority_status(
            request,
            retrieval_result.diagnostics,
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
        evidence, knowledge_selected = await assess_and_select_knowledge(
            grounding=grounding,
            context_builder=self._context_builder,
            chat_config=self._chat_config,
            question=retrieval_query,
            chunks=chunks,
            rerank_status=rerank_status,
            retrieval_config=self._retrieval_config,
            expansion_records=expansion_records,
        )

        # Capture all ORM-backed prompt inputs before closing the read
        # transaction. AsyncSession.rollback() expires ORM attributes, so
        # using ``conversation`` or ``history`` afterward would trigger
        # implicit IO from synchronous attribute access (MissingGreenlet).
        prompt_history = [
            PromptHistoryMessage(role=message.role, content=message.content) for message in history
        ]
        prompt_version = GROUNDED_PROMPT_VERSION
        template = require_prompt_template(prompt_version)
        llm = self._resolve_llm(conversation)
        temperature = self._effective_temperature(conversation)

        mode = self._chat_config.response_mode
        web_diagnostics: dict[str, Any] = {
            "status": "not_requested",
            "fallback_used": False,
        }
        web_chunks: list[ContextChunk] = []
        scoped_request = bool(request.document_id or request.metadata_filter or request.as_of)
        web_requested = (
            non_knowledge_response is None
            and scope_current_authority is None
            and (
                mode is ResponseMode.INDEXED_AND_WEB
                or (mode is ResponseMode.INDEXED_THEN_WEB and grounding.blocks_generation(evidence))
            )
        )
        # Retrieval/history reads may have opened an implicit transaction. Release it before
        # any potentially slow external web or LLM I/O.
        await self._release_read_transaction()
        if web_requested and scoped_request:
            web_diagnostics = {
                "status": "suppressed_scoped_request",
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
                web_result = await web_search.search(
                    retrieval_query,
                    max_results=self._web_search_config.max_results,
                )
                accepted_evidence, acceptance = _accepted_web_evidence(
                    retrieval_query,
                    web_result.evidence,
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
        if non_knowledge_response is not None:
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

        messages = self._prompt_builder.build(
            template=template,
            context_chunks=selected,
            history=prompt_history,
            user_question=request.content,
            domain_instructions=self._domain_instructions,
            prompt_profile=self._prompt_profile,
        )
        # Build structured notices (language-neutral; system metadata, not LLM text).
        question_language = detect_language(request.content).primary_language
        notices: list[Notice] = []
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
            web_fallback_used=web_fallback_used,
            non_knowledge_response=non_knowledge_response,
            scope_current_authority=scope_current_authority,
            notices=tuple(notices),
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
    ) -> Message:
        reason_value = str(insufficient_reason) if insufficient_reason is not None else None
        grounding = await prepared.grounding.map_claims(content, prepared.selected)
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
        metadata = self._build_metadata(
            retrieval_ms=prepared.retrieval_ms,
            generation_ms=generation_ms,
            total_ms=total_ms,
            retrieved_count=len(prepared.chunks),
            selected_count=len(prepared.selected),
            retrieval_diagnostics=prepared.retrieval_diagnostics,
            selected_chunks=prepared.selected,
        )
        evidence_gate = self._grounding.diagnostics(
            prepared.evidence,
            blocked_generation=reason_value is not None,
            generation_ran=generation_ran,
        )
        if grounding.claims_status:
            evidence_gate["claims_status"] = grounding.claims_status
        citations = (
            []
            if reason_value is not None or non_knowledge_turn
            else self._citations_for(
                prepared.selected,
                prompt_version=prepared.prompt_version,
            )
        )
        candidate_diagnostics = evidence_gate["candidate_wise"]
        selected_evidence_units = [
            chunk for chunk in prepared.selected if chunk.metadata.get("evidence_unit_id")
        ]
        cited_evidence_units = [
            citation for citation in citations if citation.get("evidence_unit_id")
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
            citations=citations,
            claims=grounding.claims,
            rerank_status=prepared.retrieval_diagnostics.get("rerank_status"),
            blocked=reason_value is not None,
            generation_ran=generation_ran,
            observe=self._chat_config.evidence_gate_mode is EvidenceGateMode.OBSERVE,
            non_knowledge_turn=non_knowledge_turn,
        )
        if not self._store_candidate_trace:
            candidate_diagnostics.pop("assessments", None)
        metadata.update(
            {
                "response_mode": self._chat_config.response_mode.value,
                "source_provenance": prepared.source_provenance.value,
                "web_search": prepared.web_search_diagnostics,
                "non_knowledge_turn": non_knowledge_turn,
                "citation_coverage": grounding.citation_coverage,
                "unverified_claim_rate": grounding.unverified_claim_rate,
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
                            ),
                        )
                    )
                ],
            }
        )
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
        log_kwargs: dict[str, Any] = {
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
                "source_provenance": prepared.source_provenance.value,
                "web_search": prepared.web_search_diagnostics,
                "execution_status": "failed",
                "execution_error_code": exc.code,
                "execution_retryable": exc.retryable,
                "grounded": False,
                "citation_coverage": 0.0,
            }
        )
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
        }

    def _citations_for(
        self,
        selected: list[ContextChunk],
        *,
        prompt_version: str,
    ) -> list[dict]:
        if not self._chat_config.include_citations:
            return []
        return build_citation_snapshots(
            selected,
            config=self._chat_config,
            project_id=self._project_id,
            config_snapshot_id=self._config_snapshot_id,
            config_provenance=self._config_provenance,
            prompt_version=prompt_version,
        )

    def _insufficient_content(self, prepared: _PreparedTurn, question: str) -> str:
        status = str(prepared.web_search_diagnostics.get("status") or "")
        bangla = detect_language(question).primary_language == "bn"
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
            },
            "retrieval_reference_date": retrieval_diagnostics.get("reference_date"),
            "retrieval_as_of": retrieval_diagnostics.get("as_of"),
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
                "non_knowledge"
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
) -> dict[str, Any] | None:
    """Detect when a hard-scope request excludes its effective modifier.

    This is now language-neutral: the English 'current' token guard has been
    removed.  Any document-scoped request where MODIFIES expansion was suppressed
    and at least one effective modifier record exists triggers a structured notice
    (not a refusal); generation proceeds from admitted scoped evidence.
    """
    if request.document_id is None:
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


def _optional_uuid(value: object) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value is not None else None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TypeError("Execution measurements must be integer-compatible values")
    return int(value)
