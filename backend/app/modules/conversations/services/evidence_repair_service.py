"""One bounded recovery pass using the existing retrieval and admission seams."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import ChatConfig, RetrievalConfig
from app.modules.conversations.context_builder import ContextBuilder
from app.modules.conversations.current_authority import (
    annotate_authority_limitations,
    remove_superseded_provisions,
)
from app.modules.conversations.grounded_context import assess_and_select_knowledge
from app.modules.conversations.grounding_service import EvidenceDecision, GroundingService
from app.modules.conversations.ports import ContextChunk, ContextRetrievalResult, RetrievalPort
from app.modules.conversations.prompts.evidence_coverage import COVERAGE_PROMPT
from app.modules.conversations.prompts.evidence_repair import (
    EVIDENCE_REPAIR_PROMPT,
    EVIDENCE_REPAIR_VERSION,
    FOCUSED_REPAIR_PROMPT,
)
from app.modules.conversations.services.evidence_coverage import (
    MAX_REPAIR_DEPENDENCIES,
    CoverageVerdict,
)
from app.modules.conversations.turn_resolution import EffectiveRetrievalInputs
from app.platform.providers.contracts.llm import (
    BaseLLMProvider,
    ChatCompletionResult,
    ChatMessage,
    ChatRole,
    ChatUsage,
)
from app.platform.providers.errors import ProviderError

REPAIR_TIMEOUT_SECONDS = 120
REPAIR_CHUNKS_PER_DEPENDENCY = 8
_SOURCE_CONTEXT_KEYS = (
    "source_title",
    "source_type",
    "source_role",
    "source_revision_id",
    "source_effective_from",
    "source_effective_to",
    "source_lifecycle_status",
    "authority_status",
    "authority_limitations",
    "heading_path",
    "section_title",
)


class _SearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[str] = Field(max_length=MAX_REPAIR_DEPENDENCIES)


@dataclass
class EvidenceRepairResult:
    selected: list[ContextChunk]
    decision: EvidenceDecision | None
    diagnostics: dict[str, Any]
    usage: ChatUsage | None = None


def _add_usage(left: ChatUsage | None, right: ChatUsage | None) -> ChatUsage:
    return ChatUsage(
        input_tokens=left.input_tokens + right.input_tokens
        if left and right and left.input_tokens is not None and right.input_tokens is not None
        else None,
        output_tokens=left.output_tokens + right.output_tokens
        if left and right and left.output_tokens is not None and right.output_tokens is not None
        else None,
    )


async def _validated_completion(
    llm: BaseLLMProvider,
    messages: list[ChatMessage],
    *,
    schema: type[BaseModel],
    temperature: float | None = None,
    max_tokens: int,
) -> ChatCompletionResult:
    """Validate provider-neutral JSON, allowing one format-only retry.

    Do not salvage partial objects or truncated output. A single enclosing Markdown
    fence is presentation only; schema and later exact-quote validation still apply.
    """
    usage = ChatUsage(0, 0)
    for attempt in range(2):
        completion = await llm.generate(messages, temperature=temperature, max_tokens=max_tokens)
        usage = _add_usage(usage, completion.usage)
        if completion.finish_reason not in {None, "stop", "completed", "end_turn"}:
            return replace(completion, usage=usage)
        content = completion.content.strip()
        lines = content.splitlines()
        if (
            len(lines) >= 3
            and lines[0].strip() in {"```json", "```"}
            and lines[-1].strip() == "```"
        ):
            content = "\n".join(lines[1:-1])
        try:
            schema.model_validate_json(content)
            return replace(completion, content=content, usage=usage)
        except ValidationError:
            if attempt:
                raise
            messages = [
                *messages,
                ChatMessage(
                    role=ChatRole.SYSTEM,
                    content="Return a complete JSON object only, matching this schema. Do not add "
                    "Markdown or commentary. Re-evaluate the original supplied evidence; "
                    "do not invent missing facts. Schema: "
                    + json.dumps(schema.model_json_schema()),
                ),
            ]
    raise AssertionError("bounded validation loop exhausted")


async def repair_knowledge_evidence(
    *,
    inputs: EffectiveRetrievalInputs,
    initial: ContextRetrievalResult,
    selected: list[ContextChunk],
    retrieval: RetrievalPort,
    llm: BaseLLMProvider,
    grounding: GroundingService,
    chat_config: ChatConfig,
    retrieval_config: RetrievalConfig,
    max_output_tokens: int,
    release_read_transaction: Callable[[], Awaitable[None]] | None = None,
    domain_instructions: str = "",
) -> EvidenceRepairResult:
    """Never mix active snapshots, relax filters, or promote unknown authority.

    All planned facets must retain an admitted unit after the final budget. Model
    queries only retrieve candidates; they never become answer evidence. A failed
    repair leaves the original authority failure available to the caller's normal
    refusal policy. Unvalidated web snippets cannot bypass it. One focused follow-up
    is allowed inside the same timeout; there is no unbounded agent loop.
    """
    diagnostics: dict[str, Any] = {"version": EVIDENCE_REPAIR_VERSION, "status": "not_attempted"}
    diagnostics["context_budget"] = {
        "max_chunks": chat_config.max_context_chunks,
        "max_characters": chat_config.context_char_budget,
    }
    result = EvidenceRepairResult([], None, diagnostics)
    reference_date = (
        inputs.as_of.date().isoformat()
        if inputs.as_of
        else initial.diagnostics.get("reference_date")
    )
    trusted_context = ""
    if reference_date:
        trusted_context += f"Trusted retrieval reference date: {reference_date}\n"
    if domain_instructions.strip():
        trusted_context += f"Trusted Project domain instructions:\n{domain_instructions.strip()}\n"
    snapshot = tuple(
        initial.diagnostics.get(k) for k in ("index_build_id", "source_metadata_generation")
    )
    if any(value is None for value in snapshot):
        diagnostics["status"] = "snapshot_unavailable"
        return result
    try:
        async with asyncio.timeout(REPAIR_TIMEOUT_SECONDS):
            # An attempted call with missing usage (including a timeout) is
            # unknown cost, not a free operation in the combined turn usage.
            result.usage = ChatUsage(None, None)
            completion = await _validated_completion(
                llm,
                [
                    ChatMessage(
                        role=ChatRole.SYSTEM, content=trusted_context + EVIDENCE_REPAIR_PROMPT
                    ),
                    ChatMessage(
                        role=ChatRole.USER,
                        content=json.dumps(
                            {
                                "question": inputs.query,
                                # Unresolved excerpts can contain obsolete/proposed
                                # numbers. Plan dependencies from the question, not
                                # those numbers; source identity only guides discovery.
                                "source_hints": [
                                    {
                                        "title": c.filename,
                                        "source": {
                                            k: c.metadata[k]
                                            for k in _SOURCE_CONTEXT_KEYS
                                            if k in c.metadata and k != "authority_limitations"
                                        },
                                    }
                                    for c in selected[:4]
                                ],
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                    ),
                ],
                temperature=None,
                max_tokens=min(1024, max_output_tokens),
                schema=_SearchPlan,
            )
            result.usage = completion.usage or ChatUsage(None, None)
            if completion.finish_reason not in {None, "stop", "completed", "end_turn"}:
                diagnostics["status"] = "incomplete_plan"
                return result
            plan = _SearchPlan.model_validate_json(completion.content)
            queries = list(dict.fromkeys(query.strip() for query in plan.queries))
            if not queries or any(not query or len(query) > 500 for query in queries):
                diagnostics["status"] = "invalid_plan"
                return result
            diagnostics["queries"] = queries
            diagnostics["branches"] = []
            groups: list[list[ContextChunk]] = []
            decisions: list[EvidenceDecision] = []
            records = list(initial.diagnostics.get("modifies_expansion_records") or [])
            pending_queries = list(queries)
            for round_index in range(2):
                for query in pending_queries:
                    # Sequential: the adapter may share one SQLAlchemy session.
                    branch = await retrieval.retrieve(
                        query=query,
                        top_k=retrieval_config.default_top_k,
                        document_id=inputs.document_id,
                        metadata_filter=inputs.metadata_filter or None,
                        as_of=inputs.as_of,
                    )
                    branch_snapshot = tuple(
                        branch.diagnostics.get(k)
                        for k in ("index_build_id", "source_metadata_generation")
                    )
                    if branch_snapshot != snapshot:
                        diagnostics["status"] = "snapshot_changed"
                        return result
                    records.extend(branch.diagnostics.get("modifies_expansion_records") or [])
                    safe = [
                        c
                        for c in remove_superseded_provisions(branch.chunks, records)
                        if c.metadata.get("authority_status") != "unresolved"
                    ]
                    decision, units = await assess_and_select_knowledge(
                        grounding=grounding,
                        context_builder=ContextBuilder(
                            chat_config.model_copy(
                                update={"max_context_chunks": REPAIR_CHUNKS_PER_DEPENDENCY}
                            )
                        ),
                        chat_config=chat_config,
                        question=query,
                        chunks=safe,
                        rerank_status=branch.diagnostics.get("rerank_status"),
                        retrieval_config=retrieval_config,
                        expansion_records=records,
                    )
                    diagnostics["branches"].append(
                        {
                            "query": query,
                            "translation": {
                                key: branch.diagnostics.get(key)
                                for key in (
                                    "translation_status",
                                    "translation_failure_reason",
                                    "translation_attempts",
                                    "translation_finish_reason",
                                    "translation_target_language",
                                    "translation_usage",
                                    "executed_branches",
                                    "branch_candidate_counts",
                                )
                            },
                            "retrieval": branch.diagnostics,
                            "selected_chunk_ids": [str(c.chunk_id) for c in units],
                        }
                    )
                    if grounding.blocks_generation(decision) or not units:
                        diagnostics["status"] = "dependency_unresolved"
                        return result
                    groups.append(units)
                    decisions.append(decision)
                # Give every dependency a first unit before adding any second units.
                ordered = [
                    group[i]
                    for i in range(REPAIR_CHUNKS_PER_DEPENDENCY)
                    for group in (
                        groups[-len(pending_queries) :] + groups[: -len(pending_queries)]
                        if round_index
                        else groups
                    )
                    if len(group) > i
                ]
                # A later branch can discover authority limitations on an earlier
                # dependency. Reconcile all relationships, including after budgeting.
                reconciled = remove_superseded_provisions(ordered, records)
                original_content = {c.chunk_id: c.content for c in ordered}
                if any(c.content != original_content[c.chunk_id] for c in reconciled):
                    # Changed spans need fresh admission; never carry the earlier
                    # similarity decision over to different evidence text.
                    diagnostics["status"] = "dependency_unresolved"
                    return result
                budgeted = annotate_authority_limitations(
                    ContextBuilder(chat_config).select(reconciled), records
                )
                if any(c.metadata.get("authority_status") == "unresolved" for c in budgeted):
                    diagnostics["status"] = "dependency_unresolved"
                    return result
                retained = {c.chunk_id for c in budgeted}
                if any(not any(c.chunk_id in retained for c in group) for group in groups):
                    diagnostics["status"] = "dependency_exceeds_budget"
                    return result
                if release_read_transaction is not None:
                    await release_read_transaction()
                planning_usage = result.usage
                result.usage = ChatUsage(None, None)
                # Short passage labels prevent the model from confusing long UUIDs
                # belonging to different excerpts of the same document. The map is
                # local to this final context; persisted provenance keeps real IDs.
                labels = {str(c.chunk_id): f"E{i}" for i, c in enumerate(budgeted, start=1)}
                source_ids = {label: source_id for source_id, label in labels.items()}
                verification = await _validated_completion(
                    llm,
                    [
                        ChatMessage(
                            role=ChatRole.SYSTEM, content=trusted_context + COVERAGE_PROMPT
                        ),
                        ChatMessage(
                            role=ChatRole.USER,
                            content=json.dumps(
                                {
                                    "original_question": inputs.query,
                                    "as_of": inputs.as_of.isoformat() if inputs.as_of else None,
                                    "queries": [
                                        {
                                            "query": query,
                                            "candidate_ids": [
                                                labels[str(c.chunk_id)]
                                                for c in group
                                                if str(c.chunk_id) in labels
                                            ],
                                        }
                                        for query, group in zip(queries, groups, strict=True)
                                    ],
                                    "authority_limitations": records,
                                    "context": [
                                        {
                                            "chunk_id": labels[str(c.chunk_id)],
                                            "chunk_index": c.chunk_index,
                                            "page_number": c.page_number,
                                            "title": c.filename,
                                            "content": c.content,
                                            "source_revision_id": c.metadata.get(
                                                "source_revision_id"
                                            ),
                                            "source": {
                                                k: c.metadata[k]
                                                for k in _SOURCE_CONTEXT_KEYS
                                                if k in c.metadata
                                            },
                                        }
                                        for c in budgeted
                                    ],
                                },
                                ensure_ascii=False,
                                # Retrieval diagnostics originate from persistence and may
                                # retain UUIDs for revision identities. They are context
                                # identifiers, not Python objects the LLM provider can use.
                                default=str,
                            ),
                        ),
                    ],
                    temperature=None,
                    max_tokens=min(4096, max_output_tokens),
                    schema=CoverageVerdict,
                )
                verification_usage = verification.usage or ChatUsage(None, None)
                result.usage = ChatUsage(
                    input_tokens=(
                        planning_usage.input_tokens + verification_usage.input_tokens
                        if planning_usage
                        and planning_usage.input_tokens is not None
                        and verification_usage.input_tokens is not None
                        else None
                    ),
                    output_tokens=(
                        planning_usage.output_tokens + verification_usage.output_tokens
                        if planning_usage
                        and planning_usage.output_tokens is not None
                        and verification_usage.output_tokens is not None
                        else None
                    ),
                )
                if verification.finish_reason not in {None, "stop", "completed", "end_turn"}:
                    diagnostics["status"] = "coverage_incomplete"
                    return result
                verdict = CoverageVerdict.model_validate_json(verification.content)
                for check in verdict.checks:
                    for quote in check.evidence:
                        quote.chunk_id = source_ids.get(quote.chunk_id, quote.chunk_id)
                # Keep quotes internal: candidate trace opt-out must not leak source
                # text through the verifier's diagnostic payload.
                diagnostics["coverage"] = {
                    "complete": verdict.complete,
                    "missing": verdict.missing,
                    "checks": [
                        {
                            "query_index": check.query_index,
                            "supported": check.supported,
                            "chunk_ids": [item.chunk_id for item in check.evidence],
                        }
                        for check in verdict.checks
                    ],
                    "quotes_validated": verdict.validates(groups, budgeted),
                }
                if verdict.validates(groups, budgeted):
                    break
                diagnostics["status"] = "coverage_incomplete"
                if round_index or verdict.complete or not verdict.missing:
                    return result
                diagnostics["initial_coverage"] = diagnostics["coverage"]
                if release_read_transaction is not None:
                    await release_read_transaction()
                previous_usage = result.usage
                result.usage = ChatUsage(None, None)
                followup = await _validated_completion(
                    llm,
                    [
                        ChatMessage(
                            role=ChatRole.SYSTEM, content=trusted_context + FOCUSED_REPAIR_PROMPT
                        ),
                        ChatMessage(
                            role=ChatRole.USER,
                            content=json.dumps(
                                {
                                    "question": inputs.query,
                                    "missing_requirements": verdict.missing,
                                    "previous_queries": queries,
                                    "discovery_excerpts": [
                                        {"title": c.filename, "content": c.content[:1500]}
                                        for i, group in enumerate(groups)
                                        if any(
                                            check.query_index == i and not check.supported
                                            for check in verdict.checks
                                        )
                                        for c in group[:4]
                                    ][:8],
                                    "source_hints": list(
                                        dict.fromkeys(c.filename for c in budgeted)
                                    ),
                                },
                                ensure_ascii=False,
                            ),
                        ),
                    ],
                    temperature=None,
                    max_tokens=min(1024, max_output_tokens),
                    schema=_SearchPlan,
                )
                result.usage = _add_usage(previous_usage, followup.usage)
                if followup.finish_reason not in {None, "stop", "completed", "end_turn"}:
                    return result
                followup_plan = _SearchPlan.model_validate_json(followup.content)
                pending_queries = list(dict.fromkeys(q.strip() for q in followup_plan.queries))[:2]
                if not pending_queries or any(not q or len(q) > 500 for q in pending_queries):
                    return result
                queries.extend(pending_queries)
                diagnostics["focused_queries"] = pending_queries
            # Discovery context can contain old/future tables and unrelated examples.
            # Hand generation the passages actually used by the validated proof,
            # instead of every superficially relevant search hit.
            proof_ids = {item.chunk_id for check in verdict.checks for item in check.evidence}
            budgeted = [c for c in budgeted if str(c.chunk_id) in proof_ids]
            if not verdict.validates(groups, budgeted):
                diagnostics["status"] = "coverage_incomplete"
                return result
            diagnostics["proof_chunk_ids"] = [str(c.chunk_id) for c in budgeted]
            result.selected = budgeted
            assessments = {a.chunk_id: a for d in decisions for a in d.candidate_assessments}
            units_by_id = {u.chunk_id: u for d in decisions for u in d.admitted_units}
            result.decision = replace(
                decisions[0],
                sufficient=True,
                reason=None,
                admitted_units=tuple(
                    units_by_id[c.chunk_id] for c in budgeted if c.chunk_id in units_by_id
                ),
                candidate_assessments=tuple(assessments.values()),
            )
            diagnostics["status"] = "recovered"
            return result
    except (ProviderError, TimeoutError, ValidationError) as exc:
        diagnostics["status"] = "repair_unavailable"
        diagnostics["failure_reason"] = (
            "timeout"
            if isinstance(exc, TimeoutError)
            else "invalid_model_response"
            if isinstance(exc, ValidationError)
            else "provider_error"
        )
        if isinstance(exc, ProviderError):
            diagnostics["provider"] = exc.provider_name
            diagnostics["error_code"] = exc.code
        elif isinstance(exc, ValidationError):
            diagnostics["validation_errors"] = [
                {"type": error["type"], "loc": list(error["loc"])}
                for error in exc.errors(include_input=False, include_url=False)
            ]
        return result
