"""One bounded recovery pass using the existing retrieval and admission seams."""

from __future__ import annotations

import asyncio
import json
import uuid
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
from app.modules.conversations.prompts.authoritative_compatibility import (
    AUTHORITATIVE_COVERAGE_PROMPT,
    AUTHORITATIVE_FOCUSED_PROMPT,
    AUTHORITATIVE_INPUT_GAP_PROMPT,
    AUTHORITATIVE_PLANNING_PROMPT,
)
from app.modules.conversations.prompts.evidence_coverage import (
    COVERAGE_PROMPT,
    PARTIAL_COVERAGE_PROMPT,
)
from app.modules.conversations.prompts.evidence_repair import (
    EVIDENCE_REPAIR_PROMPT,
    EVIDENCE_REPAIR_VERSION,
    FOCUSED_REPAIR_PROMPT,
)
from app.modules.conversations.services.evidence_coverage import (
    MAX_REPAIR_DEPENDENCIES,
    MAX_REPAIR_FOLLOWUPS,
    CoverageVerdict,
    InputGapReview,
    _contains_quote,
    _quote_tokens,
    numbered_source_lines,
)
from app.modules.conversations.turn_resolution import EffectiveRetrievalInputs
from app.platform.domain.language_detection import DEFAULT_SUPPORTED_TARGET_LANGUAGES
from app.platform.providers.contracts.llm import (
    BaseLLMProvider,
    ChatCompletionResult,
    ChatMessage,
    ChatRole,
    ChatUsage,
)
from app.platform.providers.errors import ProviderError, ProviderTimeoutError
from app.platform.providers.request_work import RequestWork

REPAIR_TIMEOUT_SECONDS = 120
REPAIR_CHUNKS_PER_DEPENDENCY = 8
_SOURCE_CONTEXT_KEYS = (
    "source_title",
    "source_type",
    "source_role",
    "source_work_key",
    "source_group_id",
    "language",
    "source_revision_id",
    "source_effective_from",
    "source_effective_to",
    "source_lifecycle_status",
    "authority_status",
    "authority_limitations",
    "heading_path",
    "section_title",
)
_AUTHORITATIVE_SOURCE_CONTEXT_KEYS = tuple(
    key for key in _SOURCE_CONTEXT_KEYS if key not in {"source_work_key", "source_group_id"}
)


class EvidenceRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=1000)


class _SearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[str] = Field(max_length=MAX_REPAIR_DEPENDENCIES)
    requirements: list[EvidenceRequirement] = Field(default_factory=list, max_length=12)
    coverage: CoverageVerdict | None = None


def _source_line_records(content: str) -> list[dict[str, Any]]:
    """Copyable selectors retain original positions; blank lines cannot prove a fact."""
    return [
        {"start_line": number, "end_line": number, "text": line}
        for number, line in enumerate(content.splitlines(), 1)
        if line.strip()
    ]


def _source_hints(
    chunks: list[ContextChunk], *, include_work_metadata: bool = False
) -> list[dict[str, Any]]:
    """A repeated high-ranking source must not hide the other corpus languages."""
    seen: set[object] = set()
    hints: list[dict[str, Any]] = []
    for chunk in chunks:
        if chunk.document_id in seen:
            continue
        seen.add(chunk.document_id)
        hints.append(
            {
                "title": chunk.filename,
                "source": {
                    key: chunk.metadata[key]
                    for key in (
                        _SOURCE_CONTEXT_KEYS
                        if include_work_metadata
                        else _AUTHORITATIVE_SOURCE_CONTEXT_KEYS
                    )
                    if key in chunk.metadata and key != "authority_limitations"
                },
            }
        )
        if len(hints) == 6:
            break
    return hints


def _search_language_instruction(
    hints: list[dict[str, Any]], evidence_approach: str = "authoritative"
) -> str:
    # Source-language concept planning is part of the existing repair call,
    # independent of the optional query-translation retrieval branches.
    if evidence_approach != "authoritative":
        languages = sorted(
            {
                str(h["source"]["language"])
                for h in hints
                if h["source"].get("language") in DEFAULT_SUPPORTED_TARGET_LANGUAGES
            }
        )
        return (
            (
                f"\nSource languages: {', '.join(languages)}. Use short source-language searches "
                "where needed, preserving explicitly requested work names. Publication recency "
                "and primary labels do not settle disagreement between independent works.\n"
            )
            if languages
            else ""
        )
    for role in ("primary", "supporting"):
        for hint in sorted(
            hints,
            key=lambda item: str(item["source"].get("source_effective_from") or ""),
            reverse=True,
        ):
            source = hint["source"]
            language = source.get("language")
            if source.get("source_role") == role and language in DEFAULT_SUPPORTED_TARGET_LANGUAGES:
                return (
                    f"\nCurrent governing sources use language code {language}. "
                    "If this differs from the user's language, include separate short queries "
                    "in each language for every necessary concept, within the eight-query limit. "
                    "Omit document titles; source identity and period are checked separately.\n"
                )
    return ""


def _discovery_excerpts(
    verdict: CoverageVerdict,
    groups: list[list[ContextChunk]],
    context: list[ContextChunk],
) -> list[dict[str, str]]:
    """Prioritize reviewer-located gaps, then interleave missing search routes.

    These are vocabulary hints only. A worked example can name a missing rule,
    but must still be replaced by governing evidence at the next coverage check.
    """
    by_id = {str(chunk.chunk_id): chunk for chunk in context}
    missing_groups = []
    for check in verdict.checks:
        if check.supported:
            continue
        pointed = [by_id[q.chunk_id] for q in check.evidence if q.chunk_id in by_id]
        candidates = groups[check.query_index] if 0 <= check.query_index < len(groups) else context
        missing_groups.append([*pointed, *candidates])
    selected: list[dict[str, str]] = []
    contents: list[str] = []
    for rank in range(max((len(group) for group in missing_groups), default=0)):
        for group in missing_groups:
            if rank >= len(group):
                continue
            chunk = group[rank]
            content = chunk.content[:2200]
            if any(content in old or old in content for old in contents):
                continue
            contents.append(content)
            selected.append({"title": chunk.filename, "content": content})
            if len(selected) == 8:
                return selected
    return selected


@dataclass
class EvidenceRepairResult:
    selected: list[ContextChunk]
    decision: EvidenceDecision | None
    diagnostics: dict[str, Any]
    usage: ChatUsage | None = None
    missing_inputs: tuple[str, ...] = ()
    partial_answer: dict[str, Any] | None = None
    failure: ProviderError | None = None


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
    proof_context: list[ContextChunk] | None = None,
    source_ids: dict[str, str] | None = None,
    truncation_retry_tokens: int | None = None,
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
            if (
                not attempt
                and completion.finish_reason == "length"
                and truncation_retry_tokens is not None
                and truncation_retry_tokens > max_tokens
            ):
                # Reasoning tokens share the output allowance on some providers.
                # Restart the bounded JSON request; never salvage partial output.
                max_tokens = truncation_retry_tokens
                work = getattr(llm, "work", None)
                if isinstance(work, RequestWork):
                    work.counts["structured_truncation_retries"] += 1
                continue
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
            parsed = schema.model_validate_json(content)
            proof = getattr(parsed, "coverage", parsed)
            if proof_context is not None and isinstance(proof, CoverageVerdict):
                lines_by_id = {str(c.chunk_id): c.content.splitlines() for c in proof_context}
                for check in proof.checks:
                    for item in check.evidence:
                        if item.start_line is None:
                            continue
                        identifier = (source_ids or {}).get(item.chunk_id, item.chunk_id)
                        lines = lines_by_id.get(identifier, [])
                        end = item.end_line or item.start_line
                        if (
                            end > len(lines)
                            or not "".join(lines[item.start_line - 1 : end]).strip()
                        ):
                            issue = ValueError(
                                f"Returned source range L{item.start_line}-L{end} "
                                "is missing or blank. Select nonempty lines "
                                "using their explicit L labels."
                            )
                            raise ValidationError.from_exception_data(
                                schema.__name__,
                                [
                                    {
                                        "type": "value_error",
                                        "loc": ("coverage",),
                                        "input": None,
                                        "ctx": {"error": issue},
                                    }
                                ],
                            )
            return replace(completion, content=content, usage=usage)
        except ValidationError as exc:
            if attempt:
                raise
            work = getattr(llm, "work", None)
            if isinstance(work, RequestWork):
                work.counts["structured_response_retries"] += 1
            messages = [
                *messages,
                ChatMessage(
                    role=ChatRole.SYSTEM,
                    content="Return a complete JSON object only, matching this schema. Do not add "
                    "Markdown or commentary. Re-evaluate the original supplied evidence; "
                    "do not invent missing facts. Schema: "
                    + json.dumps(schema.model_json_schema())
                    + " Validation issues: "
                    + json.dumps([error["msg"] for error in exc.errors(include_input=False)]),
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
    initial_decision: EvidenceDecision | None = None,
    evidence_approach: str = "authoritative",
) -> EvidenceRepairResult:
    """Never mix active snapshots, relax filters, or promote unknown authority.

    All nonempty discovery branches must retain an admitted unit after the final budget. Model
    queries only retrieve candidates; they never become answer evidence. A failed
    repair leaves the original authority failure available to the caller's normal
    refusal policy. Unvalidated web snippets cannot bypass it. Two focused follow-ups
    are allowed inside the same timeout; there is no unbounded agent loop.
    """
    authoritative_compatibility = evidence_approach == "authoritative"
    planning_prompt = (
        AUTHORITATIVE_PLANNING_PROMPT if authoritative_compatibility else EVIDENCE_REPAIR_PROMPT
    )
    coverage_prompt = (
        AUTHORITATIVE_COVERAGE_PROMPT if authoritative_compatibility else COVERAGE_PROMPT
    )
    focused_prompt = (
        AUTHORITATIVE_FOCUSED_PROMPT if authoritative_compatibility else FOCUSED_REPAIR_PROMPT
    )
    diagnostics: dict[str, Any] = {
        "version": "v17-authoritative-partial"
        if authoritative_compatibility
        else EVIDENCE_REPAIR_VERSION,
        "coverage_protocol": "authoritative_compatibility"
        if authoritative_compatibility
        else "semantic_requirements",
        "status": "not_attempted",
    }
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
    if not authoritative_compatibility:
        trusted_context += f"Evidence approach: {evidence_approach}\n"
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
            hints = _source_hints(
                [*selected, *initial.chunks],
                include_work_metadata=not authoritative_compatibility,
            )
            review_initial = (
                not authoritative_compatibility
                and bool(selected)
                and initial_decision is not None
                and not any(c.metadata.get("authority_status") == "unresolved" for c in selected)
            )
            diagnostics["initial_coverage_review"] = (
                "admitted_evidence" if review_initial else "requires_recovery"
            )
            initial_source_ids = {f"E{i}": str(c.chunk_id) for i, c in enumerate(selected, 1)}
            initial_labels = {identifier: label for label, identifier in initial_source_ids.items()}
            if not review_initial:
                # Unresolved amendments already prevent initial completeness.
                # Plan missing dependencies without a redundant review of unsafe
                # excerpts. Exact provider/content work still remains reusable.
                selected = []
            completion = await _validated_completion(
                llm,
                [
                    ChatMessage(
                        role=ChatRole.SYSTEM,
                        content=trusted_context
                        + planning_prompt
                        + _search_language_instruction(hints, evidence_approach),
                    ),
                    ChatMessage(
                        role=ChatRole.USER,
                        content=json.dumps(
                            {
                                "question": inputs.query,
                                # Unresolved excerpts can contain obsolete/proposed
                                # numbers. Plan dependencies from the question, not
                                # those numbers; source identity only guides discovery.
                                "source_hints": hints,
                                **(
                                    {
                                        "admitted_evidence": [
                                            {
                                                "chunk_id": initial_labels[str(c.chunk_id)],
                                                "title": c.filename,
                                                "source_lines": _source_line_records(c.content),
                                                "source": {
                                                    k: c.metadata[k]
                                                    for k in _SOURCE_CONTEXT_KEYS
                                                    if k in c.metadata
                                                },
                                            }
                                            for c in sorted(
                                                selected,
                                                key=lambda c: (str(c.document_id), c.chunk_index),
                                            )
                                            if review_initial
                                            and c.metadata.get("authority_status") != "unresolved"
                                        ]
                                    }
                                    if not authoritative_compatibility
                                    else {}
                                ),
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                    ),
                ],
                temperature=None,
                max_tokens=min(2048 if authoritative_compatibility else 4096, max_output_tokens),
                truncation_retry_tokens=min(2048, max_output_tokens)
                if authoritative_compatibility
                else None,
                proof_context=selected if review_initial else None,
                source_ids=initial_source_ids,
                schema=_SearchPlan,
            )
            result.usage = completion.usage or ChatUsage(None, None)
            if completion.finish_reason not in {None, "stop", "completed", "end_turn"}:
                diagnostics["status"] = "incomplete_plan"
                return result
            plan = _SearchPlan.model_validate_json(completion.content)
            requirement_ids = {r.requirement_id for r in plan.requirements}
            if len(requirement_ids) != len(plan.requirements):
                diagnostics["status"] = "invalid_plan"
                return result
            diagnostics["requirements"] = [r.model_dump() for r in plan.requirements]
            if plan.coverage is not None:
                for check in plan.coverage.checks:
                    for quote in check.evidence:
                        quote.chunk_id = initial_source_ids.get(quote.chunk_id, quote.chunk_id)
            selected = [c for c in selected if c.metadata.get("authority_status") != "unresolved"]
            if (
                requirement_ids
                and plan.coverage is not None
                and initial_decision is not None
                and plan.coverage.resolve_source_ranges(selected)
                and plan.coverage.validates([], selected, requirement_ids)
            ):
                proof_ids = {q.chunk_id for c in plan.coverage.checks for q in c.evidence}
                result.selected = [c for c in selected if str(c.chunk_id) in proof_ids]
                result.selected = annotate_authority_limitations(
                    result.selected,
                    list(initial.diagnostics.get("modifies_expansion_records") or []),
                )
                if any(c.metadata.get("authority_status") == "unresolved" for c in result.selected):
                    diagnostics["status"] = "dependency_unresolved"
                    result.selected = []
                    return result
                result.decision = replace(
                    initial_decision,
                    sufficient=True,
                    reason=None,
                    admitted_units=tuple(
                        c for c in initial_decision.admitted_units if str(c.chunk_id) in proof_ids
                    ),
                )
                diagnostics.update(
                    status="initial_evidence_complete",
                    queries=[],
                    coverage=_coverage_diagnostics(plan.coverage, True),
                )
                result.missing_inputs = tuple(plan.coverage.missing_inputs)
                return result
            if requirement_ids and plan.coverage is not None:
                # Carry proven dependencies, not every initial hit. This reserves
                # space for missing rules and avoids rediscovering supported facts.
                if not plan.coverage.resolve_source_ranges(selected):
                    diagnostics["status"] = "invalid_initial_proof"
                    return result
                sources = {str(c.chunk_id): _quote_tokens(c.content) for c in selected}
                confirmed = {
                    q.chunk_id
                    for check in plan.coverage.checks
                    if check.supported
                    and check.evidence
                    and all(
                        item.chunk_id in sources
                        and _contains_quote(sources[item.chunk_id], item.quote)
                        for item in check.evidence
                    )
                    for q in check.evidence
                }
                selected = [c for c in selected if str(c.chunk_id) in confirmed]
            queries = list(dict.fromkeys(query.strip() for query in plan.queries))
            if not queries or any(not query or len(query) > 500 for query in queries):
                diagnostics["status"] = "invalid_plan"
                return result
            diagnostics["queries"] = queries
            diagnostics["branches"] = []
            groups: list[list[ContextChunk]] = []
            raw_groups: list[list[ContextChunk]] = []
            decisions: list[EvidenceDecision] = [initial_decision] if initial_decision else []
            records = list(initial.diagnostics.get("modifies_expansion_records") or [])
            pending_queries = list(queries)
            adjacent_requests: dict[str, list[uuid.UUID]] = {}
            for round_index in range(1 + MAX_REPAIR_FOLLOWUPS):
                batch_retrieve = getattr(retrieval, "retrieve_batch", None)
                requests: list[dict[str, Any]] = [
                    dict(
                        query=query,
                        top_k=retrieval_config.default_top_k,
                        document_id=inputs.document_id,
                        metadata_filter=inputs.metadata_filter or None,
                        as_of=inputs.as_of,
                        **(
                            {"adjacent_to": adjacent_requests[query]}
                            if query in adjacent_requests
                            else {}
                        ),
                    )
                    for query in pending_queries
                ]
                if getattr(retrieval, "supports_batch_retrieval", False) is True and callable(
                    batch_retrieve
                ):
                    branches = await batch_retrieve(requests, snapshot=initial.diagnostics)
                else:
                    # Compatibility ports can share a session and must remain sequential.
                    branches = []
                    for request in requests:
                        branch = await retrieval.retrieve(**request)
                        if (
                            tuple(
                                branch.diagnostics.get(k)
                                for k in ("index_build_id", "source_metadata_generation")
                            )
                            != snapshot
                        ):
                            diagnostics["status"] = "snapshot_changed"
                            return result
                        branches.append(branch)
                for query, branch in zip(pending_queries, branches, strict=True):
                    branch_snapshot = tuple(
                        branch.diagnostics.get(k)
                        for k in ("index_build_id", "source_metadata_generation")
                    )
                    if branch_snapshot != snapshot:
                        diagnostics["status"] = "snapshot_changed"
                        return result
                    records.extend(branch.diagnostics.get("modifies_expansion_records") or [])
                    raw_groups.append(branch.chunks)
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
                            ),
                            evidence_approach=evidence_approach,
                            question=inputs.query,
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
                            "discovery_route": "adjacent"
                            if query in adjacent_requests
                            else "search",
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
                            "admission": grounding.diagnostics(
                                decision,
                                blocked_generation=grounding.blocks_generation(decision),
                                generation_ran=False,
                            ),
                            "selected_chunk_ids": [str(c.chunk_id) for c in units],
                        }
                    )
                    # A search is a discovery route, not a required source. An
                    # empty/blocked route must not abort the other dependencies.
                    groups.append([] if grounding.blocks_generation(decision) else units)
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
                # Admitted initial evidence participates in the proof; searches only fill gaps.
                ordered = (
                    [
                        *[
                            c
                            for c in selected
                            if c.metadata.get("authority_status") != "unresolved"
                        ],
                        *ordered,
                    ]
                    if requirement_ids
                    else ordered
                )
                reconciled = remove_superseded_provisions(ordered, records)
                original_content = {c.chunk_id: c.content for c in ordered}
                if any(c.content != original_content[c.chunk_id] for c in reconciled):
                    # Changed spans need fresh admission; never carry the earlier
                    # similarity decision over to different evidence text.
                    diagnostics["status"] = "dependency_unresolved"
                    return result
                budgeted = annotate_authority_limitations(
                    ContextBuilder(
                        chat_config, evidence_approach=evidence_approach, question=inputs.query
                    ).select(reconciled),
                    records,
                )
                if any(c.metadata.get("authority_status") == "unresolved" for c in budgeted):
                    diagnostics["status"] = "dependency_unresolved"
                    return result
                retained = {c.chunk_id for c in budgeted}
                if not requirement_ids and any(
                    group and not any(c.chunk_id in retained for c in group) for group in groups
                ):
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
                            role=ChatRole.SYSTEM,
                            content=trusted_context + coverage_prompt + PARTIAL_COVERAGE_PROMPT,
                        ),
                        ChatMessage(
                            role=ChatRole.USER,
                            content=json.dumps(
                                {
                                    "original_question": inputs.query,
                                    **(
                                        {"requirements": diagnostics["requirements"]}
                                        if requirement_ids
                                        else {}
                                    ),
                                    "as_of": inputs.as_of.isoformat() if inputs.as_of else None,
                                    "discovery_routes": [
                                        {
                                            "query_index": i,
                                            "candidate_ids": [
                                                labels[str(c.chunk_id)]
                                                for c in group
                                                if str(c.chunk_id) in labels
                                            ],
                                        }
                                        for i, group in enumerate(groups)
                                    ],
                                    "authority_limitations": records,
                                    "context": [
                                        {
                                            "chunk_id": labels[str(c.chunk_id)],
                                            "chunk_index": c.chunk_index,
                                            "page_number": c.page_number,
                                            "title": c.filename,
                                            **(
                                                {"content": numbered_source_lines(c.content)}
                                                if authoritative_compatibility
                                                else {
                                                    "source_lines": _source_line_records(c.content)
                                                }
                                            ),
                                            "source_revision_id": c.metadata.get(
                                                "source_revision_id"
                                            ),
                                            "source": {
                                                k: c.metadata[k]
                                                for k in (
                                                    _AUTHORITATIVE_SOURCE_CONTEXT_KEYS
                                                    if authoritative_compatibility
                                                    else _SOURCE_CONTEXT_KEYS
                                                )
                                                if k in c.metadata
                                            },
                                        }
                                        for c in (
                                            budgeted
                                            if authoritative_compatibility
                                            else sorted(
                                                budgeted,
                                                key=lambda c: (str(c.document_id), c.chunk_index),
                                            )
                                        )
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
                    proof_context=budgeted if requirement_ids else None,
                    source_ids=source_ids,
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
                if requirement_ids:
                    for check in verdict.checks:
                        if check.requirement_id and check.requirement_id not in requirement_ids:
                            if not check.description.strip():
                                diagnostics["status"] = "invalid_requirement"
                                return result
                            requirement_ids.add(check.requirement_id)
                            diagnostics["requirements"].append(
                                {
                                    "requirement_id": check.requirement_id,
                                    "description": check.description,
                                }
                            )
                for check in verdict.checks:
                    for quote in check.evidence:
                        quote.chunk_id = source_ids.get(quote.chunk_id, quote.chunk_id)
                ranges_valid = verdict.resolve_source_ranges(budgeted)
                # Preserve the established successful review/generation payloads.
                # Only an otherwise incomplete verdict with *every* source check
                # proven can ask whether the remaining gaps are personal inputs.
                candidate = verdict.model_copy(
                    update={"complete": True, "missing": [], "gap_kinds": []}
                )
                if (
                    authoritative_compatibility
                    and not verdict.complete
                    and verdict.missing
                    and ranges_valid
                    and candidate.validates(groups, budgeted, requirement_ids)
                ):
                    previous_usage = result.usage
                    result.usage = ChatUsage(None, None)
                    input_review = await _validated_completion(
                        llm,
                        [
                            ChatMessage(
                                role=ChatRole.SYSTEM,
                                content=trusted_context + AUTHORITATIVE_INPUT_GAP_PROMPT,
                            ),
                            ChatMessage(
                                role=ChatRole.USER,
                                content=json.dumps(
                                    {
                                        "original_question": inputs.query,
                                        "gaps": [
                                            {"gap_index": i, "description": gap}
                                            for i, gap in enumerate(verdict.missing)
                                        ],
                                        "quoted_evidence": [
                                            {"chunk_id": chunk_id, "quote": quote}
                                            for chunk_id, quote in dict.fromkeys(
                                                (q.chunk_id, q.quote)
                                                for check in verdict.checks
                                                for q in check.evidence
                                            )
                                        ],
                                    },
                                    ensure_ascii=False,
                                ),
                            ),
                        ],
                        schema=InputGapReview,
                        max_tokens=min(1024, max_output_tokens),
                        truncation_retry_tokens=min(2048, max_output_tokens),
                    )
                    result.usage = _add_usage(previous_usage, input_review.usage)
                    if input_review.finish_reason not in {None, "stop", "completed", "end_turn"}:
                        diagnostics["status"] = "input_review_incomplete"
                        return result
                    classification = InputGapReview.model_validate_json(input_review.content)
                    only_inputs = classification.only_inputs_for(len(verdict.missing))
                    diagnostics.setdefault("input_gap_reviews", []).append(
                        {
                            "round": round_index,
                            "original_missing": verdict.missing,
                            "gaps": classification.model_dump()["gaps"],
                            "only_inputs": only_inputs,
                        }
                    )
                    if only_inputs:
                        verdict = candidate.model_copy(
                            update={"missing_inputs": [*verdict.missing_inputs, *verdict.missing]}
                        )
                # Keep quotes internal: candidate trace opt-out must not leak source
                # text through the verifier's diagnostic payload.
                diagnostics["coverage"] = {
                    "complete": verdict.complete,
                    "missing": verdict.missing,
                    "gap_kinds": verdict.gap_kinds or ["source_rule"] * len(verdict.missing),
                    "missing_inputs": verdict.missing_inputs,
                    "checks": [
                        {
                            "query_index": check.query_index,
                            "supported": check.supported,
                            "chunk_ids": [item.chunk_id for item in check.evidence],
                            "source_ranges": [item.source_range() for item in check.evidence],
                        }
                        for check in verdict.checks
                    ],
                    "quotes_validated": ranges_valid
                    and verdict.validates(groups, budgeted, requirement_ids),
                    "source_ranges_validated": ranges_valid,
                }
                for check, payload in zip(
                    verdict.checks, diagnostics["coverage"]["checks"], strict=True
                ):
                    payload.update(
                        requirement_id=check.requirement_id, description=check.description
                    )
                if ranges_valid and verdict.validates(groups, budgeted, requirement_ids):
                    break
                if ranges_valid and verdict.partial_validates(budgeted, requirement_ids):
                    # The reviewer proved a useful independent scope. Preserve the
                    # original incomplete verdict; do not claim full recovery.
                    assert verdict.partial_answer is not None
                    diagnostics["partial_answer"] = verdict.partial_answer.model_dump()
                    # Model-authored scope prose can mention an unchecked rule.
                    # Generation receives only the identities/descriptions of the
                    # checks whose source proof actually passed.
                    diagnostics["partial_answer"]["scope"] = [
                        {"requirement_id": check.requirement_id, "description": check.description}
                        for check in verdict.checks
                        if check.requirement_id in verdict.partial_answer.requirement_ids
                    ]
                    diagnostics["partial_answer"]["pending"] = verdict.missing
                    diagnostics["partial_answer"]["gap_kinds"] = diagnostics["coverage"][
                        "gap_kinds"
                    ]
                    break
                diagnostics["status"] = "coverage_incomplete"
                if round_index == MAX_REPAIR_FOLLOWUPS or verdict.complete or not verdict.missing:
                    return result
                if not round_index:
                    diagnostics["initial_coverage"] = diagnostics["coverage"]
                diagnostics.setdefault("coverage_rounds", []).append(diagnostics["coverage"])
                # Carry only exactly cited, confirmed evidence into the next
                # round. Repeatedly carrying every earlier search hit crowds out
                # newly found governing passages and makes resolved gaps recur.
                sources = {str(c.chunk_id): _quote_tokens(c.content) for c in budgeted}
                confirmed = {
                    q.chunk_id
                    for check in verdict.checks
                    if check.supported
                    and check.evidence
                    and all(
                        item.chunk_id in sources
                        and _contains_quote(sources[item.chunk_id], item.quote)
                        for item in check.evidence
                    )
                    for q in check.evidence
                }
                groups = [[c for c in group if str(c.chunk_id) in confirmed] for group in groups]
                selected = [c for c in budgeted if str(c.chunk_id) in confirmed]
                # Read a missing rule's immediate source neighbourhood before
                # asking for another wording. Neighbours undergo the same search,
                # source policy, reranking and admission; they inherit no scores.
                adjacent_requests = {}
                if (
                    not round_index
                    and getattr(retrieval, "supports_adjacent_retrieval", False) is True
                ):
                    for check in verdict.checks:
                        i = check.query_index
                        if (
                            check.supported
                            or not check.needs_adjacent_context
                            or not check.evidence
                            or not ranges_valid
                            or (not requirement_ids and not 0 <= i < len(raw_groups))
                        ):
                            continue
                        originals = {
                            str(c.chunk_id): c.chunk_id for group in raw_groups for c in group
                        }
                        anchors = list(
                            dict.fromkeys(
                                [
                                    *(
                                        originals[q.chunk_id]
                                        for q in check.evidence
                                        if q.chunk_id in originals
                                    ),
                                ]
                            )
                        )[:4]
                        if anchors:
                            # The reviewer may cite a passage found by another
                            # route. Search the actual missing topic, rather than
                            # inheriting an unrelated route's wording or year.
                            query = verdict.missing[
                                min(len(adjacent_requests), len(verdict.missing) - 1)
                            ][:500]
                            adjacent_requests[query] = list(
                                dict.fromkeys([*adjacent_requests.get(query, []), *anchors])
                            )[:4]
                        if len(adjacent_requests) == 2:
                            break
                if adjacent_requests:
                    pending_queries = list(adjacent_requests)
                    queries.extend(pending_queries)
                    diagnostics["adjacent_queries"] = pending_queries
                    continue
                if release_read_transaction is not None:
                    await release_read_transaction()
                previous_usage = result.usage
                result.usage = ChatUsage(None, None)
                followup = await _validated_completion(
                    llm,
                    [
                        ChatMessage(role=ChatRole.SYSTEM, content=trusted_context + focused_prompt),
                        ChatMessage(
                            role=ChatRole.USER,
                            content=json.dumps(
                                {
                                    "question": inputs.query,
                                    "missing_requirements": verdict.missing,
                                    "previous_queries": queries,
                                    "discovery_excerpts": _discovery_excerpts(
                                        verdict, raw_groups, budgeted
                                    ),
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
                pending_queries = [
                    q
                    for q in dict.fromkeys(q.strip() for q in followup_plan.queries)
                    if q.casefold() not in {previous.casefold() for previous in queries}
                ][:2]
                if not pending_queries or any(not q or len(q) > 500 for q in pending_queries):
                    return result
                queries.extend(pending_queries)
                diagnostics.setdefault("focused_queries", []).extend(pending_queries)
            # Discovery context can contain old/future tables and unrelated examples.
            # Hand generation the passages actually used by the validated proof,
            # instead of every superficially relevant search hit.
            partial = diagnostics.get("partial_answer")
            proof_ids = {
                item.chunk_id
                for check in verdict.checks
                if not partial or check.requirement_id in partial["requirement_ids"]
                for item in check.evidence
            }
            budgeted = [c for c in budgeted if str(c.chunk_id) in proof_ids]
            if not (
                verdict.partial_validates(budgeted, requirement_ids)
                if partial
                else verdict.validates(groups, budgeted, requirement_ids)
            ):
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
            diagnostics["status"] = "partial_answer" if partial else "recovered"
            result.partial_answer = partial
            result.missing_inputs = tuple(verdict.missing_inputs)
            return result
    except (ProviderError, TimeoutError, ValidationError) as exc:
        if isinstance(exc, ProviderError):
            result.failure = exc
        elif isinstance(exc, TimeoutError):
            result.failure = ProviderTimeoutError(
                "Evidence review exceeded its time limit.", provider_name=llm.provider_name
            )
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
            if exc.provider_name == "retrieval" or exc.context.get("reason") in {
                "prompt_budget_exceeded",
                "embedding_identity_mismatch",
            }:
                diagnostics["failure_detail"] = exc.context.get("reason")
        elif isinstance(exc, ValidationError):
            diagnostics["validation_errors"] = [
                {"type": error["type"], "loc": list(error["loc"])}
                for error in exc.errors(include_input=False, include_url=False)
            ]
        return result


def _coverage_diagnostics(verdict: CoverageVerdict, validated: bool) -> dict[str, Any]:
    return {
        "complete": verdict.complete,
        "missing": verdict.missing,
        "missing_inputs": verdict.missing_inputs,
        "quotes_validated": validated,
        "source_ranges_validated": validated,
        "checks": [
            {
                "requirement_id": c.requirement_id,
                "description": c.description,
                "supported": c.supported,
                "chunk_ids": [q.chunk_id for q in c.evidence],
                "source_ranges": [q.source_range() for q in c.evidence],
            }
            for c in verdict.checks
        ],
    }
