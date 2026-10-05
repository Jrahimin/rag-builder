"""RAG chat orchestration with split transaction boundaries."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError

from app.core.config import (
    ChatConfig,
    EvidenceGateMode,
    LLMConfig,
    ResponseMode,
    RetrievalConfig,
    WebSearchConfig,
)
from app.core.exceptions import ServiceUnavailableError
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.modules.conversations import turn_resolution as turn_resolution_mod
from app.modules.conversations.answer_draft import (
    AnswerDraft,
    public_draft_diagnostics,
    render_verified_segments,
)
from app.modules.conversations.calculation_graph import CalculationGraph
from app.modules.conversations.citation_snapshots import build_citation_snapshots
from app.modules.conversations.context_builder import (
    ContextBuilder,
    comparison_requested,
    compliance_overview_requested,
    historical_scope_requested,
    reviewed_work_count,
)
from app.modules.conversations.current_authority import cited_authority_summary
from app.modules.conversations.execution_contracts import (
    EvidenceBundle,
    ExecutionResult,
    FinalizationResult,
    RequirementGraph,
    TurnIntent,
    assertion_from_claim,
    build_finalization,
    claim_counts,
    scope_limitations,
)
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
    _answer_segments,
)
from app.modules.conversations.notices import (
    Notice,
    draft_scope_notice,
    insufficient_evidence_notice,
    scope_excludes_effective_modifier_notice,
    unresolved_authority_notice,
    verification_failed_notice,
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
from app.modules.conversations.quantities import normalize_quantities
from app.modules.conversations.schemas.message import (
    CitationSourceKind,
    InsufficientEvidenceReason,
    MessageSendRequest,
    SourceProvenance,
    SourceScope,
)
from app.modules.conversations.services.claim_entailment_service import ClaimEntailmentService
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
from app.modules.conversations.terminal_outcome import (
    terminal_outcome,
    terminal_projection,
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
    RequestScope,
    TurnOutcome,
    TurnRelation,
    TurnResolutionInput,
    bound_resolution_history,
    normalize_request_scope,
    requires_complex_execution_budget,
)
from app.modules.conversations.turn_resolver import TurnResolver, bypass_resolution
from app.platform.domain.content_hash import content_hash
from app.platform.domain.language_detection import detect_language
from app.platform.domain.publication_integrity import complete_publication_unit
from app.platform.domain.text_tokenization import tokenize
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.contracts.llm import (
    BaseLLMProvider,
    ChatCompletionResult,
    ChatMessage,
    ChatRole,
    ChatUsage,
)
from app.platform.providers.contracts.web_search import (
    BaseWebSearchProvider,
    WebSearchEvidence,
)
from app.platform.providers.errors import ProviderError, ProviderTimeoutError
from app.platform.providers.prompt_budget import prompt_budget
from app.platform.providers.request_work import RequestWork

LOADED_RUNNER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

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
    generation_mode: str = "auto"
    originating_assistant_message_id: uuid.UUID | None = None
    inherited_coverage: dict[str, Any] | None = None
    response_policy: dict[str, Any] = field(default_factory=dict)

    execution_intent: TurnIntent | None = None
    intent: TurnIntent = field(init=False)
    requirements: RequirementGraph = field(init=False)
    bundles: tuple[EvidenceBundle, ...] = field(init=False)

    def __post_init__(self) -> None:
        scope = RequestScope.model_validate(
            self.retrieval_diagnostics.get("normalized_scope") or {}
        )
        object.__setattr__(
            self,
            "intent",
            self.execution_intent
            or TurnIntent.from_scope(
                scope,
                source_policy=str(
                    self.evidence_scope.get("effective_source_scope") or "project_default"
                ),
            ),
        )
        object.__setattr__(
            self,
            "requirements",
            RequirementGraph.from_coverage(
                self.retrieval_diagnostics.get("knowledge_repair") or {}, self.intent.scope
            ),
        )
        object.__setattr__(
            self, "bundles", tuple(EvidenceBundle.from_chunk(c) for c in self.selected)
        )


class ExecutionCancelled(asyncio.CancelledError):
    """The transport disconnected while provider output was being buffered."""


class MessageExecutionRunner:
    """Production execution; caller owns history/config/authentication and transactions."""

    def __init__(
        self,
        project_id: uuid.UUID,
        retrieval: RetrievalPort,
        chat_config: ChatConfig,
        retrieval_config: RetrievalConfig,
        llm_config: LLMConfig,
        *,
        release_read_transaction: Callable[[], Awaitable[None]],
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
        self._active_prepared: _PreparedTurn | None = None
        self._attempted_claims: list[dict[str, Any]] = []
        self._correction_attempts: list[dict[str, Any]] = []
        self._generation_content = ""
        self._work = work or RequestWork(project_id)
        self._evidence_approach = evidence_approach
        self._translation_enabled = translation_enabled
        self._release_read_transaction = release_read_transaction
        self._project_id = project_id
        self._retrieval = retrieval
        self._chat_config = chat_config
        self._retrieval_config = retrieval_config
        self._llm_config = llm_config
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

    async def prepare(
        self,
        *,
        request: MessageSendRequest,
        current_message_id: uuid.UUID,
        generation_history: list[PromptHistoryMessage],
        resolver_source: list[HistoryMessage],
        citation_chunks: dict[uuid.UUID, list[Any]],
        assistant_metadata_by_id: dict[str, dict[str, Any]],
        previous_assistant_metadata: dict[str, Any],
        llm: BaseLLMProvider,
        temperature: float | None,
    ) -> _PreparedTurn:
        self._active_prepared = None
        self._attempted_claims.clear()
        self._correction_attempts.clear()
        self._work.configure_execution(
            self._chat_config.execution_policy,
            complex_question=requires_complex_execution_budget(
                normalize_request_scope(request.content)
            ),
        )
        preparation_started = time.perf_counter()
        history_limit = self._chat_config.max_history_messages
        current_content = request.content
        non_knowledge_response = _non_knowledge_response(current_content)
        prompt_version = GROUNDED_PROMPT_VERSION
        template = require_prompt_template(
            prompt_version, evidence_approach=self._evidence_approach
        )
        request_filters = RequestFilters(
            document_id=request.document_id,
            metadata_filter=dict(request.metadata_filter or {}),
            as_of=request.as_of,
            source_scope=request.source_scope,
        )
        if request.preview_index_build_id is not None:
            pin_preview = getattr(self._retrieval, "pin_validated_preview", None)
            if not callable(pin_preview):
                raise ServiceUnavailableError(
                    message="Validated build preview is unavailable.",
                    code="preview_build_unavailable",
                )
            await pin_preview(request.preview_index_build_id)
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

        resolved = replace(
            resolved,
            retrieval=resolved.retrieval.model_copy(
                update={
                    "normalized_scope": normalize_request_scope(
                        resolved.retrieval.query,
                        reference_time=payload.reference_time,
                        request_filters=RequestFilters(
                            document_id=resolved.retrieval.document_id,
                            metadata_filter=resolved.retrieval.metadata_filter,
                            as_of=resolved.retrieval.as_of,
                            source_scope=resolved.retrieval.requested_source_scope,
                        ),
                        project_defaults=self._domain_instructions,
                    )
                }
            ),
        )
        self._work.timings["resolution"] += round((time.perf_counter() - resolution_started) * 1000)
        normalized_scope = resolved.retrieval.normalized_scope.model_copy(
            update={
                "exact_as_of": resolved.retrieval.as_of
                or resolved.retrieval.normalized_scope.exact_as_of
            }
        )
        resolved = replace(
            resolved,
            retrieval=resolved.retrieval.model_copy(update={"normalized_scope": normalized_scope}),
        )
        intent = TurnIntent.from_scope(
            normalized_scope,
            source_policy=resolved.retrieval.effective_source_scope.value,
            comparison_requested=comparison_requested(resolved.retrieval.query),
            overview_requested=compliance_overview_requested(resolved.retrieval.query),
            calculation_requested=normalized_scope.task_kind == "calculation",
            applicability_requested=_requires_current_rule_coverage(resolved.retrieval.query, []),
        )
        set_scope = getattr(self._retrieval, "set_request_scope", None)
        if callable(set_scope):
            set_scope(normalized_scope.model_dump(mode="json"))
        self._work.evidence_snapshot["normalized_scope"] = normalized_scope.model_dump(mode="json")
        diagnostics = {
            **resolved.diagnostics,
            "normalized_scope": normalized_scope.model_dump(mode="json"),
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
            retained_question = retained_rewrite_question(bounded_history, assistant_metadata_by_id)
            if followup_mode is FollowupMode.PRESENTATION_ONLY and retained_question is not None:
                # Evidence relevance is evaluated against the retained factual topic,
                # while generation still receives the current presentation request.
                retrieval_query = retained_question
                diagnostics["retained_factual_question"] = retained_question
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
                or resolved.retrieval.effective_source_scope is SourceScope.INDEXED_ONLY
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
        # These stages run sequentially. Their configured budgets must add up:
        # taking the maximum let local recovery consume the web review allowance
        # and misreported local deadline exhaustion as a web provider outage.
        evidence_deadline = self._work.recovery_deadline
        retrieval_result.diagnostics["normalized_scope"] = diagnostics.get("normalized_scope", {})
        scope_current_authority = _scope_current_authority_status(
            request,
            retrieval_result.diagnostics,
            document_id=resolved.retrieval.document_id,
        )
        query_embedder = getattr(self._retrieval, "query_embedder", None)
        grounding = GroundingService(
            self._chat_config,
            embedder=query_embedder or self._grounding._embedder,
            entailment=ClaimEntailmentService(llm),
        )
        rerank_status = str(retrieval_result.diagnostics.get("rerank_status") or "") or None
        expansion_records = list(
            retrieval_result.diagnostics.get("modifies_expansion_records") or []
        )
        self._work.evidence_snapshot["modifies_expansion_records"] = expansion_records
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
                    "new_coverage_review": False,
                }
                retrieval_result = fallback
                chunks = retrieval_result.chunks
                expansion_records = list(
                    retrieval_result.diagnostics.get("modifies_expansion_records") or []
                )
                rerank_status = str(retrieval_result.diagnostics.get("rerank_status") or "") or None
        calculation_task = False
        applicability_task = False
        unresolved_authority_obligation = False
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
            bounded_recovery_enabled = self._chat_config.bounded_recovery_enabled
            comparison_route = not presentation_only and intent.comparison_requested
            compliance_route = (
                not presentation_only
                and self._evidence_approach == "authoritative"
                and intent.overview_requested
            )
            calculation_task = intent.calculation_requested
            calculation_route = (
                not presentation_only
                and calculation_task
                and (bounded_recovery_enabled or _has_governed_calculation_source(chunks))
            )
            applicability_task = intent.applicability_requested
            applicability_route = (
                not presentation_only
                and self._evidence_approach == "authoritative"
                and applicability_task
                and (
                    _has_governed_current_rule_source(chunks)
                    or (
                        bounded_recovery_enabled
                        and (
                            self._chat_config.response_mode is ResponseMode.INDEXED_ONLY
                            or not chunks
                        )
                    )
                )
            )
            # Route selection describes the user's task, not whether the first
            # retrieval already happened to pass the evidence gate. This keeps
            # insufficient broad/current/calculation turns on the broad budget.
            review_eligible = evidence.sufficient or bounded_recovery_enabled
            comparison_review = comparison_route and review_eligible
            compliance_review = compliance_route and review_eligible
            calculation_review = calculation_route and review_eligible
            applicability_review = applicability_route and review_eligible
            unresolved_authority_obligation = bool(chunks) and (
                evidence.reason is InsufficientEvidenceReason.UNRESOLVED_AUTHORITY
                or applicability_task
                or bool(expansion_records)
            )
            relevance_repair = (
                not presentation_only
                and evidence.reason is InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD
                and evidence.best_score is not None
                and evidence.best_score >= self._chat_config.minimum_reranker_evidence_score
                and rerank_status == "applied"
            )
            reuse_scope_revalidation = bool(
                presentation_only
                and not presentation_reused
                and isinstance(inherited, dict)
                and (
                    inherited.get("coverage_partial")
                    or inherited.get("missing_inputs")
                    or inherited.get("partial_answer")
                )
                and isinstance(retrieval_result.diagnostics.get("presentation_reuse"), dict)
                and retrieval_result.diagnostics["presentation_reuse"].get("status") == "fallback"
            )
            bounded_recovery_profile = _bounded_recovery_profile(
                enabled=bounded_recovery_enabled,
                blocks_generation=grounding.blocks_generation(evidence),
                broad_task=bool(
                    comparison_route
                    or compliance_route
                    or calculation_route
                    or reuse_scope_revalidation
                ),
                presentation_only=presentation_only,
            )
            focused_recovery = bounded_recovery_profile == "focused"
            sealed_empty = retrieval_result.diagnostics.get("indexed_corpus_empty") is True
            if sealed_empty:
                retrieval_result.diagnostics["knowledge_repair"] = {
                    "status": "coverage_incomplete",
                    "stop_reason": "known_corpus_gap",
                    "coverage": {
                        "complete": False,
                        "missing": ["The pinned indexed corpus is empty"],
                    },
                }
            # An empty initial selection can still be recovered by a focused
            # query. Keep that opportunity before considering web fallback.
            if (
                (
                    evidence.reason is InsufficientEvidenceReason.UNRESOLVED_AUTHORITY
                    or calculation_review
                    or applicability_review
                    or relevance_repair
                    or comparison_review
                    or compliance_review
                    or reuse_scope_revalidation
                    or focused_recovery
                )
                and scope_current_authority is None
                and not sealed_empty
            ):
                pre_review_evidence = evidence
                # Similarity to a worked example does not prove that its category,
                # period or complete rule schedule applies to a new calculation.
                # An unsuccessful review must not fall back to those original hits.
                if (
                    calculation_review
                    or applicability_review
                    or comparison_review
                    or compliance_review
                    or reuse_scope_revalidation
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
                    bounded_recovery = self._chat_config.bounded_recovery_enabled
                    broad_recovery = bool(
                        calculation_review
                        or applicability_review
                        or comparison_review
                        or compliance_review
                        or reuse_scope_revalidation
                    )
                    if bounded_recovery:
                        broad_recovery = bounded_recovery_profile == "broad"
                    if bounded_recovery and broad_recovery:
                        repair_timeout_seconds = self._chat_config.broad_recovery_timeout_seconds
                        max_initial_queries = self._chat_config.broad_recovery_max_queries
                        max_followup_queries = self._chat_config.broad_recovery_followup_max_queries
                        max_followup_rounds = self._chat_config.broad_recovery_max_followup_rounds
                        recovery_profile = "broad"
                    elif bounded_recovery:
                        repair_timeout_seconds = self._chat_config.focused_recovery_timeout_seconds
                        max_initial_queries = self._chat_config.focused_recovery_max_queries
                        max_followup_queries = 0
                        max_followup_rounds = 0
                        recovery_profile = "focused"
                    else:
                        repair_timeout_seconds = self._llm_config.evidence_review_timeout_seconds
                        max_initial_queries = 8
                        max_followup_queries = 2
                        max_followup_rounds = 2
                        recovery_profile = "legacy"
                    if self._work.execution_policy == "adaptive_v1":
                        # The dependency came from source review; provider latency
                        # alone cannot buy a second budget or a fresh start.
                        evidence_deadline = self._work.recovery_deadline
                        repair_timeout_seconds = max(0.0, evidence_deadline - time.perf_counter())
                        max_initial_queries = 2
                        max_followup_queries = self._work.recovery_search_credits - 2
                        max_followup_rounds = 2
                        recovery_profile = "adaptive"
                    repair_timeout_seconds = min(
                        repair_timeout_seconds,
                        max(0.0, evidence_deadline - time.perf_counter()),
                    )
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
                        timeout_seconds=repair_timeout_seconds,
                        max_initial_queries=max_initial_queries,
                        max_followup_queries=max_followup_queries,
                        max_followup_rounds=max_followup_rounds,
                        recovery_profile=recovery_profile,
                        domain_instructions=self._domain_instructions,
                        initial_decision=(pre_review_evidence if compliance_review else evidence),
                        required_coverage=inherited if reuse_scope_revalidation else None,
                        evidence_approach=self._evidence_approach,
                        parallel_overview_review=bool(
                            bounded_recovery
                            and compliance_review
                            and not calculation_route
                            and not applicability_route
                            and not reuse_scope_revalidation
                        ),
                        allow_admitted_timeout_fallback=bool(
                            bounded_recovery
                            and comparison_review
                            and self._evidence_approach != "authoritative"
                            and not calculation_route
                            and not applicability_route
                            and not reuse_scope_revalidation
                        ),
                    )
                repair_usage = repaired.usage
                preparation_error = repaired.failure
                repair_diagnostics = dict(repaired.diagnostics)
                if (
                    not repaired.selected
                    and preparation_error is None
                    and time.perf_counter() >= self._work.deadline
                ):
                    preparation_error = ProviderTimeoutError(
                        "Request time limit expired before finalization",
                        provider_name=llm.provider_name,
                        context={
                            "reason": "recovery_deadline_exceeded"
                            if self._work.execution_policy == "legacy"
                            else "request_deadline_exceeded",
                            "phase": "coverage_review",
                        },
                    )
                    repair_diagnostics["stop_reason"] = preparation_error.context["reason"]
                # A local recovery deadline without validated proof is an
                # evidence limitation, not a provider outage. Preserve other
                # provider failures and keep the evidence gate closed.
                deadline_without_proof = bool(
                    bounded_recovery
                    and repair_diagnostics.get("stop_reason") == "recovery_deadline_exceeded"
                    and repaired.decision is None
                    and repaired.failure is not None
                    and repaired.failure.context.get("reason") == "recovery_deadline_exceeded"
                )
                if deadline_without_proof:
                    preparation_error = None
                    evidence = replace(
                        evidence,
                        sufficient=False,
                        reason=InsufficientEvidenceReason.UNRESOLVED_AUTHORITY,
                    )
                incomplete_review = bool(
                    repaired.decision is None
                    and repaired.failure is None
                    and repair_diagnostics.get("status")
                    in {"coverage_incomplete", "dependency_unresolved"}
                )
                authority_review_required = (
                    self._evidence_approach == "authoritative"
                    or calculation_task
                    or applicability_task
                )
                if (incomplete_review and authority_review_required) or (
                    repaired.decision is None and repaired.selected
                ):
                    evidence = replace(
                        evidence,
                        sufficient=False,
                        reason=InsufficientEvidenceReason.UNRESOLVED_AUTHORITY,
                    )
                unresolved_authority_obligation = (
                    unresolved_authority_obligation
                    or bool(repaired.selected)
                    or (authority_review_required and (deadline_without_proof or incomplete_review))
                )
                if deadline_without_proof:
                    if _reviewed_web_fallback_eligible(
                        mode=self._chat_config.response_mode,
                        provider_available=self._web_search is not None,
                        calculation_task=calculation_task,
                        applicability_task=applicability_task,
                        scoped_request=resolved.retrieval.suppress_web,
                        scope_current_authority=scope_current_authority is not None,
                        unresolved_authority=unresolved_authority_obligation,
                    ):
                        repair_diagnostics["fallback_route"] = "web_after_recovery_deadline"
                    else:
                        repair_diagnostics["fallback_route"] = (
                            "insufficient_evidence_after_recovery_deadline"
                        )
                # A completed but incomplete review may use web only when no
                # governing applicability or recovered authority remains open.
                if incomplete_review and _reviewed_web_fallback_eligible(
                    mode=self._chat_config.response_mode,
                    provider_available=self._web_search is not None,
                    calculation_task=calculation_task,
                    applicability_task=applicability_task,
                    scoped_request=resolved.retrieval.suppress_web,
                    scope_current_authority=scope_current_authority is not None,
                    unresolved_authority=unresolved_authority_obligation,
                ):
                    repair_diagnostics["fallback_route"] = "web_after_incomplete_review"
                if reuse_scope_revalidation:
                    retrieval_result.diagnostics["presentation_reuse"]["new_coverage_review"] = (
                        repair_diagnostics.get("status") != "snapshot_unavailable"
                    )
                repair_diagnostics["trigger"] = (
                    "calculation_completeness"
                    if calculation_review
                    else "presentation_reuse_scope_revalidation"
                    if reuse_scope_revalidation
                    else "compliance_overview"
                    if compliance_review
                    else "comparison_coverage"
                    if comparison_review
                    else "current_rule_applicability"
                    if applicability_review
                    else "relevance_recovery"
                    if relevance_repair
                    else "focused_lookup"
                    if focused_recovery
                    else "unresolved_authority"
                )
                if not self._store_candidate_trace:
                    repair_diagnostics["branches"] = [
                        {key: value for key, value in branch.items() if key != "retrieval"}
                        for branch in repair_diagnostics.get("branches", [])
                    ]
                retrieval_result.diagnostics["knowledge_repair"] = repair_diagnostics
                self._work.evidence_snapshot.update(
                    {
                        "knowledge_repair": repair_diagnostics,
                        "index_build_id": retrieval_result.diagnostics.get("index_build_id"),
                        "source_metadata_generation": retrieval_result.diagnostics.get(
                            "source_metadata_generation"
                        ),
                    }
                )
                if repaired.decision is not None:
                    retained_missing_inputs = (
                        tuple(str(item) for item in (inherited or {}).get("missing_inputs") or [])
                        if reuse_scope_revalidation
                        else ()
                    )
                    missing_inputs = tuple(
                        dict.fromkeys([*repaired.missing_inputs, *retained_missing_inputs])
                    )
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
                    answerable_scope = dict(repaired.answerable_scope)
                    if reuse_scope_revalidation and missing_inputs:
                        answerable_scope["missing_inputs"] = list(missing_inputs)
                    retrieval_result.diagnostics["answerable_scope"] = answerable_scope
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
        web_policy_requested = (
            non_knowledge_response is None
            and clarification_response is None
            and scope_current_authority is None
            and (
                mode is ResponseMode.INDEXED_AND_WEB
                or (mode is ResponseMode.INDEXED_THEN_WEB and grounding.blocks_generation(evidence))
            )
        )
        web_requested = web_policy_requested and preparation_error is None
        await self._release_read_transaction()
        if web_policy_requested and scoped_request:
            web_diagnostics = {
                "status": "suppressed_scoped_request",
                "fallback_used": False,
            }
        elif (
            web_policy_requested
            and evidence.reason is InsufficientEvidenceReason.UNRESOLVED_AUTHORITY
            and unresolved_authority_obligation
        ):
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
                search_budget = min(
                    self._web_search_config.request_timeout_seconds,
                    evidence_deadline - time.perf_counter(),
                )
                if partial_answer is not None:
                    # Supplementing already reviewed useful work must not add a
                    # full provider timeout before we can deliver that work.
                    search_budget = min(search_budget, 15.0)
                if search_budget <= 0:
                    raise TimeoutError
                with self._work.stage("web_search"):
                    async with asyncio.timeout(search_budget):
                        web_result = await web_search.search(
                            web_query,
                            max_results=self._web_search_config.max_results,
                        )
                accepted_evidence, acceptance = _accepted_web_evidence(
                    retrieval_query,
                    web_result.evidence,
                )
                with self._work.stage("web_evidence_review"):
                    scope_review: dict[str, object]
                    review_budget = min(20.0, evidence_deadline - time.perf_counter())
                    if review_budget <= 0:
                        accepted_evidence = []
                        scope_review = {"status": "review_timeout"}
                    else:
                        (
                            accepted_evidence,
                            scope_review,
                            web_review_usage,
                        ) = await review_web_evidence(
                            llm=llm,
                            query=retrieval_query,
                            evidence=accepted_evidence,
                            domain_instructions=self._domain_instructions,
                            reference_date=(
                                resolved.retrieval.as_of or payload.reference_time
                            ).date(),
                            timeout_seconds=review_budget,
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
                elif scope_review.get("status") in {
                    "review_timeout",
                    "review_provider_failed",
                    "review_invalid_response",
                    "review_incomplete",
                    "invalid_proof",
                }:
                    terminal_status = str(scope_review["status"])
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
            except TimeoutError:
                web_diagnostics = {
                    "status": "search_timeout",
                    "fallback_used": False,
                    "retryable": True,
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
            if knowledge_usable and web_chunks and partial_answer is not None:
                selected = _balanced_evidence(
                    knowledge_selected, web_chunks, self._context_builder, self._chat_config
                )
            else:
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
        web_only_answer = source_provenance is SourceProvenance.WEB
        web_diagnostics["fallback_used"] = web_fallback_used
        response_language = resolve_response_language(current_content, prompt_history)
        if web_only_answer and intent.overview_requested:
            # Search admission proves relevant cited passages, not that every
            # requested obligation or exception was located.
            partial_answer = _web_overview_partial_answer(response_language)
            retrieval_result.diagnostics["answerable_scope"] = {
                "complete": False,
                "partial": True,
                "unresolved_facets": partial_answer["pending"],
                "missing_inputs": [],
                "supported_requirement_ids": [],
            }

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
                partial_answer=partial_answer if knowledge_usable or web_only_answer else None,
                reviewed_scopes=(
                    (retrieval_result.diagnostics.get("answerable_scope") or {}).get(
                        "reviewed_scopes"
                    )
                    if knowledge_usable
                    else None
                ),
                response_language=response_language,
                presentation_only=presentation_only,
            )
            if any(chunk.metadata.get("reviewed_proof") for chunk in selected):
                operands = _calculation_operands(current_content, selected)
                messages.append(
                    ChatMessage(
                        role=ChatRole.SYSTEM,
                        content=(
                            "Approved calculation operand references (arithmetic only; "
                            "independently prove legal applicability): "
                        )
                        + json.dumps(
                            {
                                key: {name: str(value) for name, value in values.items()}
                                for key, values in operands.items()
                            }
                        ),
                    )
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
        if not budget["within_budget"] and evidence.sufficient:
            # Preserve the exact admitted proof. Silently truncating it could drop a tax dependency.
            # An earlier refusal already names its cause; do not replace that cause.
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
        prepared = _PreparedTurn(
            execution_intent=intent,
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
                "requested_source_scope": resolved.retrieval.requested_source_scope.value,
                "effective_source_scope": resolved.retrieval.effective_source_scope.value,
                "source_scope_origin": resolved.retrieval.source_scope_origin,
                "source_scope_reason": resolved.retrieval.source_scope_reason,
            },
            originating_assistant_message_id=_optional_uuid_or_none(originating_assistant_id),
            inherited_coverage=(
                dict(retrieval_result.diagnostics["inherited_coverage"])
                if isinstance(retrieval_result.diagnostics.get("inherited_coverage"), dict)
                else None
            ),
            generation_mode="structured_draft"
            if any(c.metadata.get("reviewed_proof") for c in selected)
            else "prose",
            response_policy=_assemble_response_policy(
                mode=mode,
                gate_mode=self._chat_config.evidence_gate_mode,
                indexed_policy=indexed_policy,
                partial_answer=partial_answer if knowledge_usable or web_only_answer else None,
                repair=(
                    None
                    if web_only_answer
                    else retrieval_result.diagnostics.get("knowledge_repair")
                ),
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
        self._active_prepared = prepared
        return prepared

    def deadline_result(
        self, request: MessageSendRequest, *, reason: str, failure_phase: str | None = None
    ) -> ExecutionResult:
        snapshot = self._work.evidence_snapshot
        phases = [
            span["name"]
            for span in self._work.snapshot()["spans"]["items"]
            if span.get("outcome") in {"cancelled", "failed"}
        ]
        phase = (
            failure_phase
            or (phases[-1] if phases else None)
            or (snapshot.get("knowledge_repair") or {}).get("phase")
            or "coverage"
        )
        outcome = terminal_outcome(
            reason=reason,
            supported_claims=0,
            partial=False,
            missing_inputs=[],
            diagnostics={**snapshot, "failure_stage": phase},
            coverage="incomplete",
        )
        scope = RequestScope.model_validate(
            snapshot.get("normalized_scope")
            or normalize_request_scope(
                request.content,
                request_filters=RequestFilters(
                    document_id=request.document_id,
                    metadata_filter=request.metadata_filter,
                    as_of=request.as_of,
                    source_scope=request.source_scope,
                ),
            ).model_dump(mode="json")
        )
        finalization = FinalizationResult(
            intent=self._active_prepared.intent
            if self._active_prepared
            else TurnIntent.from_scope(scope),
            requirements=self._active_prepared.requirements
            if self._active_prepared
            else RequirementGraph.from_coverage(snapshot.get("knowledge_repair") or {}, scope),
            evidence=list(self._active_prepared.bundles) if self._active_prepared else [],
            verified_assertions=[],
            limitations=scope_limitations(self._active_prepared.response_policy)
            if self._active_prepared
            else [],
            rejected_attempts=[
                assertion_from_claim(
                    c, i, list(self._active_prepared.bundles) if self._active_prepared else []
                )
                for i, c in enumerate(self._attempted_claims)
            ],
            correction_attempts=list(self._correction_attempts),
            terminal=outcome,
            fatal_event={"stage": outcome.failure_stage, "reason": reason},
            recovery_stop=(snapshot.get("knowledge_repair") or {}).get("stop_reason"),
        )
        language = resolve_response_language(request.content, [])
        content, finish, legacy, notices = terminal_projection(
            outcome,
            language=language,
            content="",
            finish_reason=reason,
            legacy_reason=reason,
            notices=list(snapshot.get("notices") or []),
        )
        metadata = {
            **snapshot,
            "terminal_outcome": outcome.model_dump(mode="json"),
            "execution": finalization.public_projection(),
            "operator_diagnostic": finalization.model_dump(mode="json"),
            "notices": notices,
            "evidence_summary": {"coverage": "incomplete", "claim_verification": "unverified"},
            "web_search": (
                self._active_prepared.web_search_diagnostics
                if self._active_prepared
                else snapshot.get("web_search")
                or {
                    "status": "not_attempted",
                    "fallback_used": False,
                }
            ),
            "response_policy": (
                self._active_prepared.response_policy
                if self._active_prepared
                else snapshot.get("response_policy")
                or {
                    "answerable_scope": {"complete": False, "partial": False},
                }
            ),
        }
        return ExecutionResult(
            content=content,
            finish_reason=finish,
            input_tokens=None,
            output_tokens=None,
            prompt_version="deadline.terminal.v1",
            provider=self._llm_config.backend.value,
            model=self._llm_config.model,
            metadata=metadata,
            citations=[],
            claims=[],
            grounded=False,
            insufficient_evidence_reason=legacy,
            finalization=finalization,
        )

    async def generate(
        self,
        prepared: _PreparedTurn,
        *,
        streamed: bool = False,
        should_cancel: Callable[[], Awaitable[bool]] | None = None,
    ) -> ChatCompletionResult:
        self._generation_content = ""
        self._work.complete_stage("coverage")
        with self._work.stage("answer_generation"):
            if streamed:
                contract = AnswerDraft.contract() if _requires_answer_draft(prepared) else None
                kwargs: dict[str, Any] = {
                    "temperature": prepared.temperature,
                    "max_tokens": self._llm_max_tokens(),
                }
                if contract is not None:
                    kwargs["output_contract"] = contract
                upstream = prepared.llm.stream(prepared.messages, **kwargs)
                usage = ChatUsage(None, None)
                finish = None
                try:
                    async for chunk in upstream:
                        if should_cancel is not None and await should_cancel():
                            raise ExecutionCancelled()
                        self._generation_content += chunk.delta
                        finish = chunk.finish_reason or finish
                        if chunk.usage is not None:
                            usage = chunk.usage
                finally:
                    await upstream.aclose()
                return ChatCompletionResult(
                    content=self._generation_content,
                    provider=prepared.llm.provider_name,
                    model=prepared.llm.model_name,
                    finish_reason=finish,
                    usage=usage,
                    provider_version=prepared.llm.provider_version,
                )
            if _requires_answer_draft(prepared):
                return await prepared.llm.generate_structured(
                    prepared.messages,
                    temperature=prepared.temperature,
                    max_tokens=self._llm_max_tokens(),
                    output_contract=AnswerDraft.contract(),
                )
            return await prepared.llm.generate(
                prepared.messages,
                temperature=prepared.temperature,
                max_tokens=self._llm_max_tokens(),
            )

    async def _verify_claims(
        self, prepared: _PreparedTurn, content: str, chunks: list[ContextChunk], **kwargs: Any
    ) -> GroundingResult:
        coverage = kwargs.get("coverage")
        if isinstance(coverage, dict) and prepared.requirements.requirements:
            kwargs["coverage"] = {
                **coverage,
                "requirements": [
                    {
                        "requirement_id": r.id,
                        "description": r.text,
                        "origin": r.origin,
                        "required": r.required,
                        "depends_on": r.dependencies,
                        "assigned_scope": r.assigned_scope,
                    }
                    for r in prepared.requirements.requirements
                ],
            }
        attempt = {
            "kind": "verification",
            "status": "started",
            "index": len(self._correction_attempts),
        }
        self._correction_attempts.append(attempt)
        try:
            result = await prepared.grounding.map_claims(content, chunks, **kwargs)
        except (TimeoutError, ProviderTimeoutError, asyncio.CancelledError):
            attempt["status"] = "timed_out"
            rows = kwargs.get("draft_segments") or [{"text": content}]
            self._attempted_claims.extend(
                {
                    "text": row["text"],
                    "assertion_id": row.get("assertion_id"),
                    "requirement_ids": row.get("requirement_ids", []),
                    "proof_ids": row.get("proof_ids", []),
                    "verification": "unverified",
                    "verification_reason": "verification_interrupted",
                }
                for row in rows
            )
            raise
        attempt["status"] = "completed"
        self._attempted_claims.extend(dict(c) for c in result.claims)
        return result

    async def finalize(
        self,
        *,
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
    ) -> ExecutionResult:
        self._work.complete_stage("generation")
        candidate_content = content
        draft = (
            _render_structured_answer(
                content, prepared.selected, bundles=prepared.bundles, render_citations=False
            )
            if generation_ran
            else None
        )
        if draft is not None and draft[1].get("reason") == "answer_draft_invalid":
            self._attempted_claims.append(
                {
                    "text": content,
                    "verification": "unverified",
                    "verification_reason": "answer_draft_invalid",
                }
            )
            draft = await self._correct_answer_draft(prepared, content, draft)
        if draft is not None:
            content, draft_metadata = draft
            if draft_metadata.get("status") in {"rendered", "rendered_partial"}:
                try:
                    graph = CalculationGraph.model_validate(
                        draft_metadata.get("calculations") or {}
                    )
                    operands = _calculation_operands(user_content_for_title, prepared.selected)
                    expressions = graph.expressions(operands["inputs"], operands["sources"])
                    draft_metadata["calculation_operands"] = {
                        kind: {key: str(value) for key, value in quantities.items()}
                        for kind, quantities in operands.items()
                    }
                    for row in draft_metadata["segments"]:
                        refs = row.get("calculation_references") or []
                        if any(ref not in expressions for ref in refs):
                            raise ValueError("Unknown calculation node")
                        if not refs and re.search(
                            r"\d[^\n]*[+*=\u00d7\u00f7/][^\n]*\d", row["text"]
                        ):
                            raise ValueError("Calculation assertion requires its audited graph")
                        if refs:
                            if len(refs) != 1 or _canonical_math(row["text"]) != _canonical_math(
                                expressions[refs[0]]
                            ):
                                raise ValueError("Assertion is not the verified graph expression")
                            row["text"] = expressions[refs[0]]
                            row["calculation_verified"] = True
                except ValueError:
                    draft_metadata.update(
                        status="failed_verification", reason="calculation_graph_invalid"
                    )
            prepared.retrieval_diagnostics["answer_draft"] = draft_metadata
        verification_started = time.perf_counter()
        reason_value = str(insufficient_reason) if insufficient_reason is not None else None
        if draft is not None and draft[1].get("status") == "failed_verification":
            reason_value = str(InsufficientEvidenceReason.CLAIM_VERIFICATION_FAILED)
            prepared.retrieval_diagnostics["rejected_draft"] = {
                "candidate_count": len(draft[1].get("segments") or []),
                "failed_count": len(draft[1].get("segments") or []),
                "failure_reason_count": 1,
                "reasons": [draft[1].get("reason") or "draft_verification_not_completed"],
            }
        attempted_claims = self._attempted_claims
        verification_repair: dict[str, Any] | None = None
        if (
            clarification_turn
            or non_knowledge_turn
            or not generation_ran
            or reason_value is not None
        ):
            grounding = GroundingResult(
                claims=[],
                grounded=None if clarification_turn else False,
                citation_coverage=0.0,
                claims_status="not_applicable",
            )
        else:
            with self._work.stage("claim_verification"):
                grounding = await self._verify_claims(
                    prepared,
                    content,
                    prepared.selected,
                    draft_segments=(prepared.retrieval_diagnostics.get("answer_draft") or {}).get(
                        "segments"
                    ),
                    user_input=user_content_for_title,
                    coverage=(
                        prepared.inherited_coverage
                        or prepared.retrieval_diagnostics.get("knowledge_repair")
                    ),
                )
            if (
                grounding.grounded is False
                and reason_value is None
                and generation_ran
                # A failed verifier protocol is not a semantic rejection. Copying
                # quotes cannot turn unavailable verification into a completed
                # verifier stage or hide its failure from the publication gate.
                and not any(
                    claim.get("verification_reason")
                    in {"verifier_schema_invalid", "verifier_unavailable"}
                    for claim in grounding.claims
                )
                and self._work.claim_correction("semantic")
            ):
                # One deterministic repair retains proven independent facts and
                # re-verifies exact approved source statements for rejected IDs.
                rows = (prepared.retrieval_diagnostics.get("answer_draft") or {}).get(
                    "segments"
                ) or []
                repaired_rows = []
                for row in rows:
                    claim = next(
                        (
                            c
                            for c in grounding.claims
                            if c.get("assertion_id") == row["assertion_id"]
                        ),
                        None,
                    )
                    if claim and claim.get("verification") == "supported":
                        repaired_rows.append(row)
                        continue
                    quotes = [
                        item.get("quote")
                        for bundle in prepared.bundles
                        if str(bundle.chunk_id) in row["proof_ids"]
                        for item in bundle.applicability_proof
                        if item.get("requirement_id") in row["requirement_ids"]
                        and item.get("quote")
                        and str(item["quote"]) in bundle.text
                    ]
                    if quotes:
                        repaired_rows.append(
                            {**row, "text": " ".join(dict.fromkeys(str(q) for q in quotes))}
                        )
                if not rows:
                    verified = [
                        claim
                        for claim in grounding.claims
                        if claim.get("verification") == "supported"
                    ]
                    cited_ids = {
                        str(item.get("chunk_id"))
                        for claim in grounding.claims
                        for item in claim.get("evidence", [])
                    }
                    repair_sources = [
                        (index, chunk)
                        for index, chunk in enumerate(prepared.selected, 1)
                        if str(chunk.chunk_id) in cited_ids
                    ]
                    if not verified and repair_sources:
                        repaired_content = "\n\n".join(
                            chunk.content + f" [{index}]" for index, chunk in repair_sources
                        )
                        with self._work.stage("semantic_repair"):
                            reviewed = await self._verify_claims(
                                prepared,
                                repaired_content,
                                prepared.selected,
                                user_input=user_content_for_title,
                                coverage=prepared.inherited_coverage
                                or prepared.retrieval_diagnostics.get("knowledge_repair"),
                            )
                        if reviewed.grounded is True:
                            content, grounding = repaired_content, reviewed
                            verification_repair = {
                                "status": "deterministic_verified_facts",
                                "attempts": 1,
                            }
                    if verified:
                        content = "\n\n".join(
                            str(claim["text"])
                            + "".join(
                                f" [{index}]"
                                for index in dict.fromkeys(
                                    item["citation_index"] for item in claim.get("evidence", [])
                                )
                            )
                            for claim in verified
                        )
                        grounding = GroundingResult(
                            claims=verified, grounded=True, citation_coverage=1.0
                        )
                        verification_repair = {"status": "retained_verified_facts", "attempts": 1}
                        prepared.response_policy["answerable_scope"]["partial"] = True
                        prepared.response_policy["answerable_scope"]["complete"] = False
                if repaired_rows:
                    for row in repaired_rows:
                        refs = row.get("calculation_references") or []
                        if refs:
                            registry = _calculation_operands(
                                user_content_for_title, prepared.selected
                            )
                            expressions = CalculationGraph.model_validate(
                                prepared.retrieval_diagnostics["answer_draft"].get("calculations")
                                or {}
                            ).expressions(registry["inputs"], registry["sources"])
                            if (
                                len(refs) != 1
                                or refs[0] not in expressions
                                or _canonical_math(row["text"])
                                != _canonical_math(expressions[refs[0]])
                            ):
                                row["calculation_verified"] = False
                                repaired_rows = []
                                break
                if repaired_rows:
                    with self._work.stage("semantic_repair"):
                        reviewed = await self._verify_claims(
                            prepared,
                            "\n\n".join(row["text"] for row in repaired_rows),
                            prepared.selected,
                            draft_segments=repaired_rows,
                            user_input=user_content_for_title,
                            coverage=prepared.inherited_coverage
                            or prepared.retrieval_diagnostics.get("knowledge_repair"),
                        )
                    if reviewed.grounded is True:
                        grounding = reviewed
                        prepared.retrieval_diagnostics["answer_draft"]["segments"] = repaired_rows
                        verification_repair = {
                            "status": "deterministic_verified_facts",
                            "attempts": 1,
                        }
            if grounding.grounded is True and draft is not None:
                verified_ids = {
                    str(claim["assertion_id"])
                    for claim in grounding.claims
                    if claim.get("verification") == "supported"
                    and isinstance(claim.get("assertion_id"), str)
                }
                rows = [
                    row
                    for row in prepared.retrieval_diagnostics["answer_draft"]["segments"]
                    if row["assertion_id"] in verified_ids
                ]
                prepared.retrieval_diagnostics["answer_draft"]["segments"] = rows
                indexes = {str(chunk.chunk_id): i for i, chunk in enumerate(prepared.selected, 1)}
                content = render_verified_segments(
                    rows, supported_ids=verified_ids, proof_indexes=indexes
                )
                for row in prepared.retrieval_diagnostics["answer_draft"].get("notices", []):
                    prepared = replace(
                        prepared,
                        notices=(
                            *prepared.notices,
                            draft_scope_notice(
                                kind=row["kind"],
                                language=prepared.response_language,
                                proof_ids=row["proof_ids"],
                            ),
                        ),
                    )
                    self._active_prepared = prepared
            self._work.complete_stage("verification")
            if grounding.grounded is False and reason_value is None and generation_ran:
                failed_claims = [
                    claim for claim in grounding.claims if claim.get("verification") != "supported"
                ]
                failed_reasons = sorted(
                    {
                        str(claim.get("verification_reason") or claim.get("verification"))
                        for claim in failed_claims
                    }
                )
                # Publication requires supported assertions on both ordinary
                # and reviewed paths; a similarity failure is still a rejection.
                model_rejected = bool(failed_claims)
                if model_rejected:
                    retained_claims = [
                        claim
                        for claim in grounding.claims
                        if claim.get("verification") == "supported"
                    ]
                    prepared.retrieval_diagnostics["rejected_draft"] = {
                        "candidate_count": len(grounding.claims),
                        "failed_count": len(failed_claims),
                        "failure_reason_count": len(failed_reasons),
                        "reasons": failed_reasons[:12],
                    }
                    if (
                        "verifier_schema_invalid" in failed_reasons
                        or "verifier_unavailable" in failed_reasons
                    ):
                        prepared.retrieval_diagnostics["verification_failure"] = next(
                            item for item in failed_reasons if item.startswith("verifier_")
                        )
                    if retained_claims:
                        # A rejected requirement must not erase independent claims
                        # that passed the same source and semantic verifier.
                        grounding = GroundingResult(
                            claims=retained_claims,
                            grounded=True,
                            citation_coverage=grounding.citation_coverage,
                        )
                        content = _render_supported_claims(retained_claims)
                        if draft is not None:
                            retained_ids = {c.get("assertion_id") for c in retained_claims}
                            prepared.retrieval_diagnostics["answer_draft"]["segments"] = [
                                row
                                for row in prepared.retrieval_diagnostics["answer_draft"][
                                    "segments"
                                ]
                                if row["assertion_id"] in retained_ids
                            ]
                        prepared.response_policy["answerable_scope"]["partial"] = True
                        prepared.response_policy["answerable_scope"]["complete"] = False
                        verification_repair = {
                            "status": "retained_verified_facts",
                            "failed_claim_reasons": failed_reasons,
                        }
                    else:
                        content = "The generated answer could not be verified."
                        reason_value = InsufficientEvidenceReason.CLAIM_VERIFICATION_FAILED.value
                        grounding = GroundingResult(
                            claims=[], grounded=False, citation_coverage=0.0
                        )
                        verification_repair = {
                            "status": "withheld_unverified_answer",
                            "failed_claim_reasons": failed_reasons,
                        }
            if reason_value is not None:
                grounding = type(grounding)(
                    claims=[], grounded=False, citation_coverage=0.0, claims_status="not_applicable"
                )
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
        if generation_ran and draft is not None and not attempted_claims:
            try:
                candidate = json.loads(candidate_content)
            except ValueError:
                candidate = None
            if isinstance(candidate, dict) and isinstance(candidate.get("segments"), list):
                attempted_claims.extend(
                    [
                        {
                            "assertion_id": row.get("assertion_id"),
                            "text": str(row.get("text") or ""),
                            "requirement_ids": [str(v) for v in row.get("requirement_ids", [])],
                            "proof_ids": [str(v) for v in row.get("proof_ids", [])],
                            "claim_kind": "source_assertion",
                            "verification": "unverified",
                            "verification_reason": str(
                                draft[1].get("reason") or "draft_verification_not_completed"
                            ),
                            "verification_method": "draft_schema",
                        }
                        for row in (draft[1].get("segments") or candidate["segments"])[:60]
                        if isinstance(row, dict)
                    ]
                )
        verification_ms = round((time.perf_counter() - verification_started) * 1000)
        self._work.timings["generation"] = generation_ms
        total_ms += verification_ms
        if generation_ran and grounding.grounded is True:
            complete_claims = [
                claim for claim in grounding.claims if _publication_claim_complete(claim, prepared)
            ]
            rejected_publication = [
                claim for claim in grounding.claims if claim not in complete_claims
            ]
            if rejected_publication or finish_reason in {
                "length",
                "max_tokens",
                "max_output_tokens",
            }:
                prepared.retrieval_diagnostics["publication_integrity"] = {
                    "status": "partial" if complete_claims else "rejected",
                    "reason": "incomplete_assertion_or_unknown_completeness",
                    "rejected_assertion_ids": [
                        claim.get("assertion_id") for claim in rejected_publication
                    ],
                }
                prepared.response_policy["answerable_scope"].update(partial=True, complete=False)
                for claim in rejected_publication:
                    attempted_claims.append(
                        {
                            **claim,
                            "grounded": False,
                            "verification": "unverified",
                            "verification_reason": "incomplete_publication",
                        }
                    )
                grounding = GroundingResult(
                    claims=complete_claims,
                    grounded=bool(complete_claims),
                    citation_coverage=1.0 if complete_claims else 0.0,
                )
                content = _render_supported_claims(complete_claims)
                if not complete_claims:
                    prepared.retrieval_diagnostics["verification_failure"] = (
                        "incomplete_publication"
                    )
                    reason_value = InsufficientEvidenceReason.CLAIM_VERIFICATION_FAILED.value
                if draft is not None:
                    complete_ids = {claim.get("assertion_id") for claim in complete_claims}
                    prepared.retrieval_diagnostics["answer_draft"]["segments"] = [
                        row
                        for row in prepared.retrieval_diagnostics["answer_draft"]["segments"]
                        if row["assertion_id"] in complete_ids
                    ]
        metadata = self._build_metadata(
            retrieval_ms=prepared.retrieval_ms,
            generation_ms=generation_ms,
            total_ms=total_ms,
            retrieved_count=len(prepared.chunks),
            selected_count=len(prepared.selected),
            retrieval_diagnostics=prepared.retrieval_diagnostics,
            selected_chunks=prepared.selected,
        )
        if verification_repair is not None:
            metadata["verification_repair"] = verification_repair
        if grounding.grounded is True and _has_incomplete_trailing_fragment(content):
            prepared.retrieval_diagnostics["verification_failure"] = "incomplete_publication"
            prepared.retrieval_diagnostics["publication_integrity"] = {
                "status": "rejected",
                "reason": "incomplete_trailing_fragment",
            }
            content = "The generated answer ended mid-sentence and could not be verified."
            reason_value = InsufficientEvidenceReason.CLAIM_VERIFICATION_FAILED.value
            grounding = GroundingResult(claims=[], grounded=False, citation_coverage=0.0)
            verification_repair = {
                "status": "withheld_incomplete_answer",
                "reason": "incomplete_trailing_fragment",
            }
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
            blocked_generation=reason_value is not None and not generation_ran,
            generation_ran=generation_ran,
        )
        if grounding.claims_status:
            evidence_gate["claims_status"] = grounding.claims_status
        citations = (
            []
            if reason_value is not None
            or non_knowledge_turn
            or clarification_turn
            or (verification_repair or {}).get("status") == "withheld_unverified_answer"
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
                "source_scope": {
                    "requested": prepared.evidence_scope.get("requested_source_scope"),
                    "effective": prepared.evidence_scope.get("effective_source_scope"),
                    "origin": prepared.evidence_scope.get("source_scope_origin"),
                    "reason": prepared.evidence_scope.get("source_scope_reason"),
                },
                "verification_version": "claim-verification-v2",
                "web_search": prepared.web_search_diagnostics,
                "non_knowledge_turn": non_knowledge_turn,
                "citation_coverage": grounding.citation_coverage,
                "unverified_claim_rate": grounding.unverified_claim_rate,
                "unsupported_claim_rate": (
                    unsupported_claims / factual_total if factual_total else 0.0
                ),
                "attempted_claim_verification_counts": claim_counts(attempted_claims),
                "published_claim_verification_counts": claim_counts(grounding.claims),
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
                        (
                            *prepared.notices,
                            verification_failed_notice(
                                language=detect_language(user_content_for_title).primary_language
                                or "en"
                            ),
                        )
                        if (verification_repair or {}).get("status") == "withheld_unverified_answer"
                        else prepared.notices
                        if reason_value is None
                        else (
                            *prepared.notices,
                            (
                                verification_failed_notice(
                                    language=detect_language(
                                        user_content_for_title
                                    ).primary_language
                                    or "en"
                                )
                                if _coverage_verification_failed(prepared.retrieval_diagnostics)
                                else insufficient_evidence_notice(
                                    language=detect_language(
                                        user_content_for_title
                                    ).primary_language
                                    or "en"
                                )
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
        repair_partial = (
            bool(
                (prepared.retrieval_diagnostics.get("knowledge_repair") or {}).get("partial_answer")
            )
            or (prepared.response_policy.get("answerable_scope") or {}).get("partial") is True
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
        from app.modules.conversations.execution_contracts import published_requirement_fulfillment

        publication = published_requirement_fulfillment(prepared.requirements, grounding.claims)
        prepared.retrieval_diagnostics["published_requirements"] = publication
        omitted = publication["omitted_requirements"]
        publication_notices = []
        if publication["assessed"] and omitted:
            metadata["evidence_summary"]["coverage"] = (
                "partial" if supported_claims else "incomplete"
            )
            for citation in citations:
                citation["coverage_status"] = metadata["evidence_summary"]["coverage"]
                citation["coverage_partial"] = bool(supported_claims)
            descriptions = _format_user_facing_gap_details(
                [item["description"] for item in omitted]
            )
            publication_notices.append(
                {
                    "kind": "unfulfilled_requirements",
                    "language": prepared.response_language,
                    "text": (
                        "যাচাইকৃত উত্তরে এই অংশগুলো সম্পন্ন হয়নি: "
                        if prepared.response_language == "bn"
                        else "The verified answer does not complete these requested parts: "
                    )
                    + descriptions,
                    "source": {"requirements": omitted},
                }
            )
            metadata["notices"].extend(publication_notices)
        metadata["published_requirements"] = publication
        prepared.retrieval_diagnostics["generation_ran"] = generation_ran
        outcome = terminal_outcome(
            reason=reason_value,
            supported_claims=supported_claims,
            partial=repair_partial or inherited_partial,
            missing_inputs=metadata["evidence_summary"]["input_provenance"]["unresolved_inputs"],
            diagnostics=prepared.retrieval_diagnostics,
            coverage=metadata["evidence_summary"]["coverage"],
            clarification=clarification_turn,
            non_knowledge=non_knowledge_turn,
        )
        if verification_repair is not None:
            prepared.retrieval_diagnostics["verification_repair"] = verification_repair
        content, citations = _published_citations(content, citations, grounding.claims)
        finalization = build_finalization(prepared, grounding.claims, attempted_claims, outcome)
        finalization = finalization.model_copy(
            update={
                "limitations": finalization.limitations + publication_notices,
                "correction_attempts": list(self._correction_attempts)
                + finalization.correction_attempts,
            }
        )
        metadata["execution"] = finalization.public_projection()
        metadata["citation_coverage_status"] = (
            "applicable" if finalization.verified_assertions else "not_applicable"
        )
        provenance_fields = {
            "provider",
            "model",
            "reasoning",
            "schema_mode",
            "purpose",
            "schema_name",
            "schema_hash",
            "endpoint_hash",
            "capability_revision",
            "capability_source",
            "local_validation",
            "span_id",
            "status",
        }
        metadata["provider_provenance"] = [
            {k: v for k, v in call.items() if k in provenance_fields}
            for call in self._work.calls
            if call.get("kind") == "llm"
        ]
        metadata["operator_diagnostic"] = finalization.model_dump(mode="json")
        metadata["terminal_outcome"] = outcome.model_dump(mode="json")
        metadata["evidence_funnel"]["outcome"] = (
            "clarification" if clarification_turn else outcome.outcome
        )
        if prepared.retrieval_diagnostics.get("rejected_draft"):
            metadata["rejected_draft"] = prepared.retrieval_diagnostics["rejected_draft"]
        if outcome.failure_stage == "draft_schema":
            metadata["rejected_draft"] = {
                **metadata.get("rejected_draft", {}),
                "candidate_count": (prepared.retrieval_diagnostics.get("answer_draft") or {}).get(
                    "candidate_count", 0
                ),
                "issues": (prepared.retrieval_diagnostics.get("answer_draft") or {}).get(
                    "issues", []
                )[:12],
            }
        content, finish_reason, reason_value, metadata["notices"] = terminal_projection(
            outcome,
            language=prepared.response_language,
            content=content,
            finish_reason=finish_reason,
            legacy_reason=reason_value,
            notices=metadata.get("notices") or [],
        )
        return ExecutionResult(
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
            finalization=finalization,
        )

    async def _correct_answer_draft(
        self, prepared: _PreparedTurn, content: str, failure: tuple[str, dict[str, Any]]
    ) -> tuple[str, dict[str, Any]]:
        if self._work.phase_deadline(
            "answer_shape_correction"
        ) - time.perf_counter() < 2 or not self._work.claim_correction("malformed"):
            return failure
        self._work.counts["answer_shape_corrections"] += 1
        try:
            with self._work.stage("answer_shape_correction"):
                async with asyncio.timeout(
                    max(
                        0.0,
                        self._work.phase_deadline("answer_shape_correction") - time.perf_counter(),
                    )
                ):
                    result = await prepared.llm.generate_structured(
                        [
                            *prepared.messages,
                            ChatMessage(role=ChatRole.ASSISTANT, content=content),
                            ChatMessage(
                                role=ChatRole.SYSTEM,
                                content="Correct only the JSON shape of the prior draft. "
                                "Keep the same assertions, requirement IDs and proof IDs. "
                                "Do not add facts or change assertion scope. "
                                "Use only immutable approved proof in this prompt. "
                                + AnswerDraft.instructions(),
                            ),
                        ],
                        output_contract=AnswerDraft.contract(),
                        temperature=None,
                        max_tokens=self._llm_max_tokens(),
                    )
            repaired = _render_structured_answer(
                result.content, prepared.selected, bundles=prepared.bundles, render_citations=False
            )
            if repaired is None:
                return failure
            if repaired[1].get("status") == "rendered":
                try:
                    original = json.loads(content)
                except ValueError:
                    original = None
                original_segments = (
                    original.get("segments") if isinstance(original, dict) else original
                )
                keys = (
                    "assertion_id",
                    "text",
                    "requirement_ids",
                    "proof_ids",
                    "calculation_references",
                )
                if isinstance(original_segments, list):
                    original_segments = [
                        {
                            **item,
                            "calculation_references": item.get("calculation_references", []),
                            "assertion_id": item.get("assertion_id")
                            or "A"
                            + hashlib.sha256(
                                (str(i) + ":" + str(item.get("text", ""))).encode()
                            ).hexdigest()[:20],
                        }
                        if isinstance(item, dict)
                        else item
                        for i, item in enumerate(original_segments)
                    ]
                    flattened = []
                    for item in original_segments:
                        if not isinstance(item, dict):
                            flattened.append(item)
                            continue
                        atoms = [
                            str(atom).strip()
                            for atom in _answer_segments(str(item.get("text", "")))
                            if str(atom).strip()
                        ]
                        flattened.extend(
                            {
                                **item,
                                "text": atom,
                                "assertion_id": item["assertion_id"]
                                + (f".{j + 1}" if len(atoms) > 1 else ""),
                            }
                            for j, atom in enumerate(atoms)
                        )
                    original_segments = flattened
                original_bindings = (
                    [tuple(item.get(key) for key in keys) for item in original_segments]
                    if isinstance(original_segments, list)
                    and all(isinstance(item, dict) for item in original_segments)
                    else None
                )
                repaired_bindings = [
                    tuple(item.get(key) for key in keys) for item in repaired[1]["segments"]
                ]
                changed_extra = isinstance(original, dict) and (
                    original.get("notices", []) != repaired[1].get("notices", [])
                    or original.get("calculations", {"nodes": []})
                    != repaired[1].get("calculations", {"nodes": []})
                )
                if (
                    original_bindings is None
                    or original_bindings != repaired_bindings
                    or changed_extra
                ):
                    failure[1]["shape_correction"] = "rejected_assertion_or_proof_change"
                    return failure
            repaired[1]["shape_correction"] = "completed"
            return repaired
        except (ProviderTimeoutError, TimeoutError) as exc:
            failure[1]["shape_correction"] = "failed"
            failure[1]["timeout_reason"] = (
                exc.context.get("reason", "provider_timeout")
                if isinstance(exc, ProviderTimeoutError)
                else "request_deadline_exceeded"
            )
            return failure
        except ProviderError:
            failure[1]["shape_correction"] = "failed"
            return failure

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
        repair = prepared.retrieval_diagnostics.get("knowledge_repair") or {}
        if repair.get("stop_reason") == "known_corpus_gap":
            missing = (repair.get("coverage") or {}).get("missing") or []
            details = _format_user_facing_gap_details(missing)
            prefix = (
                "সক্রিয় সূত্রসমষ্টিতে এই প্রমাণগুলো অনুপস্থিত: "
                if bangla
                else "The active corpus is missing this required proof: "
            )
            return prefix + details
        missing = (repair.get("coverage") or {}).get("missing") or []
        if missing and repair.get("status") in {"coverage_incomplete", "partial_answer"}:
            return "The indexed evidence does not establish: " + _format_user_facing_gap_details(
                missing
            )
        if status == "search_timeout":
            return (
                ("ওয়েব অনুসন্ধানের সময়সীমা শেষ হয়েছে, তাই যথেষ্ট সূত্র যাচাই করা যায়নি। আবার চেষ্টা করুন।")
                if bangla
                else "Web search reached its time limit before enough sources could be verified. "
                "Please try again."
            )
        if status in {"failed", "provider_unavailable"}:
            if bangla:
                return "উপলব্ধ সূত্র দিয়ে এই উত্তরটি যাচাই করা যায়নি, এবং web search এখন সাময়িকভাবে অনুপলব্ধ।"
            return (
                "I couldn't verify this answer from the available sources, and web search "
                "is temporarily unavailable."
            )
        if status in {
            "review_timeout",
            "review_provider_failed",
            "review_invalid_response",
            "review_incomplete",
            "invalid_proof",
        }:
            return (
                "ওয়েব সূত্র যাচাইয়ের ধাপটি সম্পন্ন হয়নি। অনুগ্রহ করে আবার চেষ্টা করুন।"
                if bangla
                else "Web source verification did not complete. Please try again."
            )
        repair = prepared.retrieval_diagnostics.get("knowledge_repair") or {}
        if repair.get("fallback_route") == "insufficient_evidence_after_recovery_deadline":
            return (
                "সূত্র যাচাইয়ের সময়সীমা শেষ হয়েছে, তাই এই উত্তরটির জন্য যথেষ্ট প্রমাণ "
                "যাচাই করতে পারিনি। প্রশ্নটি আরও নির্দিষ্ট করে আবার চেষ্টা করুন।"
                if bangla
                else "Source review reached its time limit before I could verify enough "
                "evidence for this answer. Please try a more specific question."
            )
        if prepared.evidence.reason is InsufficientEvidenceReason.UNRESOLVED_AUTHORITY:
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
            "normalized_scope": retrieval_diagnostics.get("normalized_scope", {}),
            "answer_draft": public_draft_diagnostics(retrieval_diagnostics.get("answer_draft")),
            "retrieval_configuration_hash": retrieval_diagnostics.get("configuration_hash"),
        }

    def _llm_max_tokens(self) -> int:
        return self._llm_config.max_tokens


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
    ("web_search", ("finding_relevant_sources", "Searching web sources")),
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
        "retained_factual_question",
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
        or (partial_answer or {}).get("exclusions")
        or coverage.get("missing")
        or []
    )
    if not unresolved and repair_payload.get("status") == "repair_unavailable":
        unresolved = [
            str(item.get("description"))
            for item in repair_payload.get("requirements") or []
            if isinstance(item, dict) and item.get("description")
        ]
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


def _reviewed_web_fallback_eligible(
    *,
    mode: ResponseMode,
    provider_available: bool,
    calculation_task: bool,
    applicability_task: bool,
    scoped_request: bool,
    scope_current_authority: bool,
    unresolved_authority: bool = False,
) -> bool:
    """Apply the same scope and task guards to recovery-triggered web fallback."""
    return (
        mode in {ResponseMode.INDEXED_THEN_WEB, ResponseMode.INDEXED_AND_WEB}
        and provider_available
        and not calculation_task
        and not applicability_task
        and not scoped_request
        and not scope_current_authority
        and not unresolved_authority
    )


def _web_overview_partial_answer(language: str = "en") -> dict[str, Any]:
    if language == "bn":
        scope = "উদ্ধৃত ওয়েব সূত্রে সরাসরি সমর্থিত তথ্য"
        exclusion = "উদ্ধৃত সূত্রে সমর্থনহীন প্রশ্নের অংশ।"
        pending = "প্রশ্নের সব অংশের জন্য সূত্রের পূর্ণতা যাচাই করা হয়নি।"
    else:
        scope = "Facts directly supported by the cited web passages"
        exclusion = "Parts of the question that the cited passages do not establish."
        pending = "Source coverage for every part of the question has not been verified."
    return {
        "scope": scope,
        "requirement_ids": [],
        "exclusions": [exclusion],
        "pending": [pending],
        "gap_kinds": ["source_rule"],
        "supported_proof": [],
    }


def _coverage_verification_failed(retrieval_diagnostics: dict[str, Any]) -> bool:
    repair = retrieval_diagnostics.get("knowledge_repair")
    return bool(
        isinstance(repair, dict)
        and repair.get("status") == "repair_unavailable"
        and repair.get("failure_reason") == "invalid_model_response"
    )


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
    del chunks
    return bool(
        re.search(
            r"\b(?:calculat(?:e|ed|ing|ion|ions)|recalculat(?:e|ion)|"
            r"comput(?:e|ing|ation)|breakdown)\b|\bhow much\b|হিসাব|হিসেব|গণনা|পরিগণনা",
            question,
            re.IGNORECASE,
        )
    )


def _has_governed_calculation_source(chunks: list[ContextChunk]) -> bool:
    return any(
        chunk.metadata.get("source_role") in {"primary", "supporting"}
        and chunk.metadata.get("source_lifecycle_status") in {"active", "retired"}
        for chunk in chunks
    )


def _bounded_recovery_profile(
    *,
    enabled: bool,
    blocks_generation: bool,
    broad_task: bool,
    presentation_only: bool,
) -> str | None:
    """Choose a bounded budget from task shape before considering sufficiency."""
    if not enabled:
        return None
    if broad_task:
        return "broad"
    if blocks_generation and not presentation_only:
        return "focused"
    return None


def _requires_current_rule_coverage(question: str, chunks: list[ContextChunk]) -> bool:
    """Changing rules need scope proof even when the user omits "current".

    Include reference sources: a highly similar proposal/company rule must not
    bypass applicability review merely because no governing source was selected.
    This also runs when conversation interpretation falls back to the raw question.
    """
    del chunks
    governed_fact = re.search(
        r"\b(?:rate|rule|requirement|deadline|tax|fee|penalty|duty|applicab|guidance|"
        r"refund|filing|return|limit|threshold|exemption|slab|cap|policy)\w*\b"
        r"|হার|বিধান|নিয়ম|নিয়ম|সময়সীমা|সময়সীমা|কর|ফি|"
        r"জরিমানা|দায়িত্ব|দায়িত্ব|প্রযোজ্য|রিটার্ন|দাখিল|সীমা|করমুক্ত|নীতিমালা",
        question,
        re.I,
    )
    temporal = re.search(
        r"\b(?:current|currently|latest|today|now|(?:19|20)\d{2})\b"
        r"|বর্তমান|সর্বশেষ|এখন",
        question,
        re.I,
    )
    if temporal and governed_fact:
        return True
    # A single changing numeric/provision fact has a current default even if
    # the request is terse or imperative. Broader policy summaries keep their
    # existing coverage route so they do not all incur a rule review.
    changing_fact = re.search(
        r"\b(?:rate|limit|threshold|exemption|slab|cap|deadline|fee|penalty|duty)\w*\b"
        r"|হার|সীমা|করমুক্ত|সময়সীমা|সময়সীমা|ফি|জরিমানা",
        question,
        re.I,
    )
    return bool(changing_fact)


def _has_governed_current_rule_source(chunks: list[ContextChunk]) -> bool:
    return any(
        chunk.metadata.get("source_role") in {"primary", "supporting", "reference"}
        and chunk.metadata.get("source_lifecycle_status") in {"active", "retired"}
        for chunk in chunks
    )


def _prune_unverified_partial_paragraphs(content: str, claims: list[dict[str, Any]]) -> str:
    """Keep only whole paragraphs whose claims all passed verification."""
    invalid = [
        str(claim.get("text") or "") for claim in claims if claim.get("verification") != "supported"
    ]
    if not invalid or any(not item or item not in content for item in invalid):
        return ""
    paragraphs = re.split(r"\n\s*\n", content)
    retained = [
        paragraph.strip()
        for paragraph in paragraphs
        if paragraph.strip() and not any(item in paragraph for item in invalid)
    ]
    return "\n\n".join(retained)


def _coverage_scope_fallback(repair: dict[str, Any] | None, selected: list[ContextChunk]) -> str:
    """Use only fully reviewed scopes, then run the normal claim verifier again."""
    coverage = (repair or {}).get("coverage")
    if not isinstance(coverage, dict) or coverage.get("complete") is not True:
        return ""
    checks = coverage.get("checks")
    if not isinstance(checks, list) or not checks:
        return ""
    by_id = {str(chunk.chunk_id): index for index, chunk in enumerate(selected, start=1)}
    paragraphs: list[str] = []
    for check in checks:
        if (
            not isinstance(check, dict)
            or check.get("supported") is not True
            or check.get("fulfillment") != "full"
            or check.get("unresolved_facets")
        ):
            return ""
        scope = str(check.get("answerable_scope") or "").strip()
        proof_ids = check.get("chunk_ids")
        if not scope or not isinstance(proof_ids, list):
            return ""
        indexes = list(dict.fromkeys(by_id[item] for item in proof_ids if item in by_id))
        if not indexes:
            return ""
        markers = " ".join(f"[{index}]" for index in indexes)
        sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", scope) if item.strip()]
        paragraphs.append(" ".join(f"{item.rstrip('.!?')}. {markers}" for item in sentences))
    return "\n\n".join(paragraphs)


def _repair_missing_citation_claims(
    content: str,
    failed_claims: list[dict[str, Any]],
    uncited_claims: list[dict[str, Any]],
) -> str:
    """Attach only citations independently verified against selected evidence."""
    by_id = {claim.get("claim_id"): claim for claim in uncited_claims}
    repaired = content
    for failed in failed_claims:
        match = by_id.get(failed.get("claim_id"))
        claim_text = str(failed.get("text") or "")
        evidence = match.get("evidence") if isinstance(match, dict) else None
        if (
            not claim_text
            or content.count(claim_text) != 1
            or not isinstance(match, dict)
            or match.get("verification") != "supported"
            or not isinstance(evidence, list)
            or not evidence
        ):
            return ""
        citation_index = evidence[0].get("citation_index")
        if not isinstance(citation_index, int) or citation_index < 1:
            return ""
        repaired = repaired.replace(claim_text, f"{claim_text} [{citation_index}]", 1)
    return repaired if repaired != content else ""


def _requires_answer_draft(prepared: _PreparedTurn) -> bool:
    return prepared.generation_mode == "structured_draft" or (
        prepared.generation_mode == "auto"
        and any(chunk.metadata.get("reviewed_proof") for chunk in prepared.selected)
    )


def _user_facing_gap_details(values: list[Any]) -> list[str]:
    """Collapse coverage diagnostics into concise, non-identifier user language."""
    clean: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = re.sub(r"\s+", " ", str(raw)).strip(" ;\t\r\n")
        value = re.sub(r"\bR\d+\s*:\s*", "", value)
        value = re.sub(r"\bunproven governing dependency\s+R\d+\b", "", value, flags=re.I)
        value = re.sub(r"\b(?:requirement|dependency)\s+R\d+\b", "", value, flags=re.I)
        value = re.sub(r"\s+", " ", value).strip(" ;\t\r\n")
        if not value.strip(" .;!?") or re.fullmatch(r"R\d+", value, flags=re.I):
            continue
        key = value.casefold().rstrip(" .;!?")
        if key not in seen:
            seen.add(key)
            clean.append(value[:400])
    return clean or ["the applicable source rule and its period"]


def _format_user_facing_gap_details(values: list[Any]) -> str:
    """Join complete gap descriptions without removing or duplicating punctuation."""
    return " ".join(
        detail if re.search(r"[.!?\u0964\u0965\u3002\uff01\uff1f]$", detail) else detail + "."
        for detail in _user_facing_gap_details(values)
    )


def _has_incomplete_trailing_fragment(text: str) -> bool:
    """Catch a one-character cut-off after a complete-looking generation result."""
    # Strip rendered citation markers before inspecting the final prose, so a
    # citation added by the renderer cannot conceal a cut-off source assertion.
    text = re.sub(r"(?:\s*\[\d+\])+\s*$", "", text.strip())
    return bool(
        re.search(
            r"\b(?:subsequent|increased|reduced|and|or|to|of|for|with|that|which)\s+"
            r"[a-z]$|\b(?:and|or|to|of|for|with|that|which|because|including|subsequent)\s*$",
            text.strip(),
            re.I,
        )
    )


def _render_supported_claims(claims: list[dict[str, Any]]) -> str:
    """Render only the published verdict set, with its own evidence markers."""
    return "\n\n".join(
        re.sub(r"\s*\[\d+\]", "", str(claim["text"])).strip()
        + "".join(
            f" [{index}]"
            for index in dict.fromkeys(item["citation_index"] for item in claim.get("evidence", []))
        )
        for claim in claims
        if claim.get("verification") == "supported"
    )


def _publication_claim_complete(claim: dict[str, Any], prepared: _PreparedTurn) -> bool:
    text = str(claim.get("text") or "")
    if _has_incomplete_trailing_fragment(text):
        return False
    if claim.get("claim_kind") == "coverage_scope":
        return True
    # These are explicit structural closure signals, never a prose quality score.
    structured = (
        claim.get("verification_method") == "literal_source_identity"
        and text.strip().startswith("|")
        and re.sub(r"(?:\s*\[\d+\])+\s*$", "", text).strip().endswith("|")
    ) or bool(
        claim.get("calculation_references") and claim.get("arithmetic_verification") == "supported"
    )
    plain = re.sub(r"\s*\[\d+\]", "", text).strip()
    if claim.get("verification_method") == "literal_source_identity":
        cited_ids = {str(item.get("chunk_id")) for item in claim.get("evidence", [])}
        for chunk in prepared.selected:
            if str(chunk.chunk_id) not in cited_ids:
                continue
            for source_span in chunk.metadata.get("source_spans", []):
                table = str(source_span.get("text") or "").strip()
                rows = [
                    row.strip().strip("|").split("|") for row in table.splitlines() if "|" in row
                ]
                if (
                    source_span.get("role") == "table"
                    and table
                    and plain.endswith(table)
                    and len(rows) >= 2
                    and len({len(row) for row in rows}) == 1
                    and all(all(cell.strip() for cell in row) for row in rows)
                ):
                    structured = True
    semantic_complete = prepared.grounding.publication_completeness.get(
        str(claim.get("assertion_id"))
    )
    if structured:
        return complete_publication_unit(text, structured=True, semantic_complete=semantic_complete)
    atoms = _answer_segments(text)
    return bool(atoms) and all(
        complete_publication_unit(str(atom), semantic_complete=semantic_complete)
        and not _has_incomplete_trailing_fragment(str(atom))
        for atom in atoms
    )


def _published_citations(
    content: str, citations: list[dict[str, Any]], claims: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """Persist only used citation records; bind previews to published claim spans.

    The indexed evidence hashes and local offsets remain intact for replay.
    User locators and previews come from the actual verified claim evidence.
    """
    used = {int(value) for value in re.findall(r"\[(\d+)\]", content)}
    retained: list[dict[str, Any]] = []
    remap: dict[int, int] = {}
    for old_index, citation in enumerate(citations, 1):
        if old_index not in used:
            continue
        remap[old_index] = len(retained) + 1
        evidence = [
            item
            for claim in claims
            if claim.get("verification") == "supported"
            for item in claim.get("evidence", [])
            if item.get("citation_index") == old_index
        ]
        snapshot = dict(citation)
        if evidence:
            excerpts = list(
                dict.fromkeys(str(item["excerpt"]) for item in evidence if item.get("excerpt"))
            )
            snapshot["excerpt"] = " … ".join(excerpts) or None
            requirements = {
                identity
                for claim in claims
                if any(
                    item.get("citation_index") == old_index for item in claim.get("evidence", [])
                )
                for identity in claim.get("requirement_ids", [])
            }
            snapshot["supporting_spans"] = [
                item
                for item in citation.get("supporting_spans", [])
                if item.get("requirement_id") in requirements
            ]
            locations = {(item.get("char_start"), item.get("char_end")) for item in evidence}
            if len(locations) == 1:
                snapshot["char_start"], snapshot["char_end"] = next(iter(locations))
            else:
                snapshot["char_start"] = snapshot["char_end"] = None
            pages = {item.get("page_number") for item in evidence}
            snapshot["page_number"] = next(iter(pages)) if len(pages) == 1 else None
            snapshot["provenance_precision"] = (
                "exact_source_span"
                if len(locations) == 1 and all(value is not None for value in next(iter(locations)))
                else "multiple_source_spans"
                if any(item.get("char_start") is not None for item in evidence)
                else "unknown_source_locator"
            )
            if len(locations) > 1:
                snapshot["supporting_spans"] += [
                    {
                        "role": "published_claim_span",
                        "excerpt": item.get("excerpt"),
                        "page_number": item.get("page_number"),
                        "char_start": item.get("char_start"),
                        "char_end": item.get("char_end"),
                        "evidence_span_hash": item.get("evidence_span_hash"),
                    }
                    for item in evidence
                ]
        retained.append(snapshot)
    content = re.sub(
        r"\[(\d+)\]", lambda match: f"[{remap.get(int(match[1]), int(match[1]))}]", content
    )
    for claim in claims:
        claim["text"] = re.sub(
            r"\[(\d+)\]",
            lambda match: f"[{remap.get(int(match[1]), int(match[1]))}]",
            str(claim.get("text") or ""),
        )
        for item in claim.get("evidence", []):
            if item.get("citation_index") in remap:
                item["citation_index"] = remap[item["citation_index"]]
    return content, retained


def _calculation_operands(user_input: str, chunks: list[ContextChunk]) -> dict[str, dict[str, Any]]:
    def values(text: str) -> dict[str, Any]:
        return {
            f"q{i}": item.value / 100 if item.kind == "rate" else item.value
            for i, item in enumerate(normalize_quantities(text))
            if item.kind not in {"period", "locator"}
        }

    return {
        "inputs": values(user_input),
        "sources": {
            f"{chunk.chunk_id}:{key}": value
            for chunk in chunks
            if chunk.metadata.get("reviewed_proof")
            for key, value in values(
                " ".join(
                    str(item.get("quote"))
                    for item in chunk.metadata["reviewed_proof"]
                    if item.get("quote") and str(item["quote"]) in chunk.content
                )
            ).items()
        },
    }


def _canonical_math(text: str) -> str:
    return re.sub(
        r"\s+", "", text.replace("\u00d7", "*").replace("\u00f7", "/").replace("\u2212", "-")
    )


def _render_structured_answer(
    content: str,
    chunks: list[ContextChunk],
    *,
    bundles: tuple[EvidenceBundle, ...] | None = None,
    render_citations: bool = True,
) -> tuple[str, dict[str, Any]] | None:
    """Canonical schema parsing and immutable approved proof references."""
    required = any(chunk.metadata.get("reviewed_proof") for chunk in chunks)
    candidate = content.strip()
    if candidate.startswith(chr(96) * 3 + "json") and candidate.endswith(chr(96) * 3):
        candidate = candidate[7:-3].strip()
    if not required and not candidate.startswith("{"):
        return None
    try:
        draft = AnswerDraft.model_validate_json(candidate)
    except ValidationError as exc:
        if not required:
            return None
        return "The answer draft could not be verified.", {
            "version": "answer.draft.v1",
            "status": "failed_verification",
            "reason": "answer_draft_invalid",
            "issues": [
                {"type": item["type"], "path": ".".join(str(p) for p in item["loc"])}
                for item in exc.errors(include_input=False)[:12]
            ],
            "candidate_count": 0,
        }
    segments: list[dict[str, Any]] = []
    for i, item in enumerate(draft.segments):
        atoms = [str(atom).strip() for atom in _answer_segments(item.text) if str(atom).strip()]
        segments.extend(
            {
                **item.model_dump(),
                "text": atom,
                "assertion_id": item.stable_id(i) + (f".{j + 1}" if len(atoms) > 1 else ""),
            }
            for j, atom in enumerate(atoms)
        )
    if len({item["assertion_id"] for item in segments}) != len(segments):
        return "The answer draft could not be verified.", {
            "status": "failed_verification",
            "reason": "duplicate_assertion_id",
            "candidate_count": len(segments),
        }
    indexes = {str(chunk.chunk_id): index for index, chunk in enumerate(chunks, 1)}
    admitted = (
        bundles if bundles is not None else tuple(EvidenceBundle.from_chunk(c) for c in chunks)
    )
    approved = {
        (str(item.get("requirement_id")), str(bundle.chunk_id))
        for bundle in admitted
        for item in bundle.applicability_proof
        if item.get("quote") and str(item["quote"]) in bundle.text
    }
    # A proof reference outside the selected evidence set is a protocol/scope
    # violation for the whole draft. Partial rendering is reserved for known
    # evidence whose reviewed requirement proof is incomplete; it must never
    # make a draft containing foreign evidence look like a safe partial answer.
    if any(
        str(proof_id) not in indexes for segment in segments for proof_id in segment["proof_ids"]
    ):
        return "The answer draft could not be verified.", {
            "version": draft.version,
            "status": "failed_verification",
            "reason": "foreign_proof_reference",
            "candidate_count": len(segments),
        }
    rendered = []
    approved_segments: list[dict[str, Any]] = []
    rejected_segment_count = 0
    for segment in segments:
        proof, requirements = segment["proof_ids"], segment["requirement_ids"]
        if (
            any(item not in indexes for item in proof)
            or bool(proof) != bool(requirements)
            or any(not any((item, source) in approved for source in proof) for item in requirements)
            or any(not any((item, source) in approved for item in requirements) for source in proof)
            or re.search(r"\[\d+\]", segment["text"])
        ):
            rejected_segment_count += 1
            continue
        if not proof:
            rejected_segment_count += 1
            continue
        approved_segments.append(segment)
        rendered.append(
            segment["text"].strip()
            + (
                "".join(f" [{indexes[item]}]" for item in dict.fromkeys(proof))
                if render_citations
                else ""
            )
        )
    if not rendered:
        return "The answer draft could not be verified.", {
            "version": draft.version,
            "status": "failed_verification",
            "reason": "no_approved_assertions",
            "candidate_count": len(segments),
            "rejected_segment_count": rejected_segment_count,
        }
    if any(any(proof not in indexes for proof in notice.proof_ids) for notice in draft.notices):
        return "The answer draft could not be verified.", {
            "status": "failed_verification",
            "reason": "notice_proof_invalid",
            "candidate_count": len(segments),
        }
    return "\n\n".join(rendered), {
        "version": draft.version,
        "status": "rendered_partial" if rejected_segment_count else "rendered",
        "segments": approved_segments,
        "candidate_count": len(segments),
        "rejected_segment_count": rejected_segment_count,
        "notices": [item.model_dump() for item in draft.notices],
        "calculations": draft.calculations.model_dump(mode="json"),
    }
