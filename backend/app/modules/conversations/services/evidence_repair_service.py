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
)
from app.modules.conversations.services.evidence_coverage import CoverageVerdict
from app.modules.conversations.turn_resolution import EffectiveRetrievalInputs
from app.platform.providers.contracts.llm import BaseLLMProvider, ChatMessage, ChatRole, ChatUsage
from app.platform.providers.errors import ProviderError

REPAIR_TIMEOUT_SECONDS = 60


class _SearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[str] = Field(max_length=3)


@dataclass
class EvidenceRepairResult:
    selected: list[ContextChunk]
    decision: EvidenceDecision | None
    diagnostics: dict[str, Any]
    usage: ChatUsage | None = None


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
) -> EvidenceRepairResult:
    """Never mix active snapshots, relax filters, or promote unknown authority.

    All planned facets must retain an admitted unit after the final budget. Model
    queries only retrieve candidates; they never become answer evidence. A failed
    repair leaves the original authority failure available to the caller's normal
    web recovery/refusal policy. No retries or unbounded agent loop.
    """
    diagnostics: dict[str, Any] = {"version": EVIDENCE_REPAIR_VERSION, "status": "not_attempted"}
    result = EvidenceRepairResult([], None, diagnostics)
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
            completion = await llm.generate(
                [
                    ChatMessage(role=ChatRole.SYSTEM, content=EVIDENCE_REPAIR_PROMPT),
                    ChatMessage(
                        role=ChatRole.USER,
                        content=json.dumps(
                            {
                                "question": inputs.query,
                                "evidence": [
                                    {
                                        "title": c.filename,
                                        "excerpt": c.content[:1200],
                                        "limitations": c.metadata.get("authority_limitations", []),
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
            for query in queries:
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
                        chat_config.model_copy(update={"max_context_chunks": 2})
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
            ordered = [group[i] for i in range(2) for group in groups if len(group) > i]
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
            verification = await llm.generate(
                [
                    ChatMessage(role=ChatRole.SYSTEM, content=COVERAGE_PROMPT),
                    ChatMessage(
                        role=ChatRole.USER,
                        content=json.dumps(
                            {
                                "original_question": inputs.query,
                                "as_of": inputs.as_of.isoformat() if inputs.as_of else None,
                                "queries": [
                                    {
                                        "query": query,
                                        "candidate_ids": [str(c.chunk_id) for c in group],
                                    }
                                    for query, group in zip(queries, groups, strict=True)
                                ],
                                "authority_limitations": records,
                                "context": [
                                    {
                                        "chunk_id": str(c.chunk_id),
                                        "title": c.filename,
                                        "content": c.content,
                                        "source_revision_id": c.metadata.get("source_revision_id"),
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
            if not verdict.validates(groups, budgeted):
                diagnostics["status"] = "coverage_incomplete"
                return result
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
    except (ProviderError, TimeoutError, ValidationError):
        diagnostics["status"] = "repair_unavailable"
        return result
