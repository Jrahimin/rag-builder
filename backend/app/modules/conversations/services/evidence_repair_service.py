"""One bounded recovery pass using the existing retrieval and admission seams."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import date
from time import monotonic as _unbound_monotonic
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.core.config import ChatConfig, RetrievalConfig
from app.modules.conversations.context_builder import ContextBuilder
from app.modules.conversations.current_authority import (
    annotate_authority_limitations,
    authority_record_affects_chunk,
    remove_superseded_provisions,
)
from app.modules.conversations.execution_contracts import Requirement as EvidenceRequirement
from app.modules.conversations.grounded_context import assess_and_select_knowledge
from app.modules.conversations.grounding_service import EvidenceDecision, GroundingService
from app.modules.conversations.ports import (
    ContextChunk,
    ContextRetrievalResult,
    EvidenceScope,
    RetrievalPort,
)
from app.modules.conversations.prompts.authoritative_compatibility import (
    AUTHORITATIVE_COVERAGE_PROMPT,
    AUTHORITATIVE_FOCUSED_PROMPT,
    AUTHORITATIVE_INPUT_GAP_PROMPT,
    AUTHORITATIVE_PLANNING_PROMPT,
)
from app.modules.conversations.prompts.evidence_coverage import (
    COVERAGE_PROMPT,
    DELTA_COVERAGE_PROMPT,
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
    CoverageDelta,
    CoverageVerdict,
    InputGapReview,
    PartialAnswerScope,
    _Check,
    _contains_quote,
    _quote_tokens,
    numbered_source_lines,
)
from app.modules.conversations.services.recovery_schedule import RecoverySchedule
from app.modules.conversations.turn_resolution import (
    EffectiveRetrievalInputs,
    normalize_request_scope,
)
from app.platform.domain.content_hash import content_hash
from app.platform.domain.language_detection import (
    DEFAULT_SUPPORTED_TARGET_LANGUAGES,
    detect_language,
)
from app.platform.providers.contracts.llm import (
    BaseLLMProvider,
    ChatCompletionResult,
    ChatMessage,
    ChatRole,
    ChatUsage,
    StructuredOutput,
    generate_structured,
)
from app.platform.providers.errors import ProviderError, ProviderTimeoutError
from app.platform.providers.request_work import RequestWork, current_request_work


def monotonic() -> float:
    return time.perf_counter() if current_request_work() is not None else _unbound_monotonic()


_RECOVERY_SCHEDULE: ContextVar[RecoverySchedule | None] = ContextVar(
    "recovery_schedule", default=None
)

REPAIR_TIMEOUT_SECONDS = 300
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
    "parse_quality_score",
    "partial_extraction",
    "source_text_truncated",
    "extraction_warnings",
)
_AUTHORITATIVE_SOURCE_CONTEXT_KEYS = tuple(
    key for key in _SOURCE_CONTEXT_KEYS if key not in {"source_work_key", "source_group_id"}
)


class _SearchQuery(BaseModel):
    """A discovery route with explicit ownership of the requirements it attempts."""

    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)
    requirement_ids: list[str] = Field(default_factory=list, max_length=12)


class _SearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[_SearchQuery] = Field(max_length=MAX_REPAIR_DEPENDENCIES)
    requirements: list[EvidenceRequirement] = Field(default_factory=list, max_length=12)
    coverage: Any = None

    @field_validator("queries", mode="before")
    @classmethod
    def accept_legacy_query_strings(cls, value: Any) -> Any:
        """Keep old providers/tests readable while the wire format moves to bound queries."""
        if not isinstance(value, list):
            return value
        return [
            {"query": item, "requirement_ids": []} if isinstance(item, str) else item
            for item in value
        ]

    @model_validator(mode="after")
    def drop_invalid_optional_coverage(self) -> _SearchPlan:
        if self.coverage is None:
            return self
        try:
            self.coverage = CoverageVerdict.model_validate(self.coverage)
        except ValidationError:
            self.coverage = None
        return self

    @model_validator(mode="after")
    def reject_invalid_dependencies(self) -> _SearchPlan:
        """Reject unknown or circular claims before they can contaminate proof scope."""
        by_id = {item.requirement_id: item for item in self.requirements}
        if len(by_id) != len(self.requirements):
            raise ValueError("Requirement IDs must be unique.")
        visited: set[str] = set()
        active: set[str] = set()

        def visit(requirement_id: str) -> None:
            if requirement_id in active:
                raise ValueError("Requirement dependencies must not contain a cycle.")
            if requirement_id in visited:
                return
            active.add(requirement_id)
            for dependency in by_id[requirement_id].depends_on:
                if dependency not in by_id:
                    raise ValueError(f"Unknown requirement dependency: {dependency}.")
                visit(dependency)
            active.remove(requirement_id)
            visited.add(requirement_id)

        for requirement_id in by_id:
            visit(requirement_id)
        return self


_PROVISION_ANCHOR = re.compile(
    r"(?<!\w)(?:sections?|subsections?|sec\.?|ধারা|উপধারা)\s*"
    r"(?P<number>\d+(?:\s*\([\dA-Za-z\u0980-\u09FF]+\))*)",
    re.IGNORECASE,
)


def _normalize_discovery_query(query: str, user_question: str) -> tuple[str, str | None]:
    """Drop ungrounded provision precision only from the initial search plan."""
    supplied = {
        "".join(str(int(ch)) if ch.isdecimal() else ch for ch in match.group("number"))
        for match in _PROVISION_ANCHOR.finditer(user_question)
    }
    removed = False

    def replace_anchor(match: re.Match[str]) -> str:
        nonlocal removed
        number = "".join(str(int(ch)) if ch.isdecimal() else ch for ch in match.group("number"))
        if number in supplied:
            return match.group(0)
        removed = True
        return " "

    executable = " ".join(_PROVISION_ANCHOR.sub(replace_anchor, query).split())
    return executable, "model_added_provision_anchor_removed" if removed else None


def _prepare_search_plan(
    plan: _SearchPlan,
    user_question: str = "",
    proven_ids: set[str] | None = None,
    *,
    balanced_overview: bool = False,
) -> tuple[list[EvidenceRequirement], list[str], dict[str, list[str]]]:
    """Drop optional work and return deduplicated executable routes with ownership."""
    proven = proven_ids or set()
    requirements = [
        requirement
        for requirement in plan.requirements
        if not _optional_requirement(requirement, user_question)
    ]
    retained_ids = {item.requirement_id for item in requirements}
    requirements = [
        item.model_copy(
            update={"depends_on": [dep for dep in item.depends_on if dep in retained_ids]}
        )
        for item in requirements
    ]
    requirements.sort(key=_requirement_priority)
    allowed = {requirement.requirement_id for requirement in requirements}
    optional = {
        requirement.requirement_id
        for requirement in plan.requirements
        if _optional_requirement(requirement, user_question)
    }
    queries: list[str] = []
    ownership: dict[str, list[str]] = {}
    keys: dict[str, str] = {}
    for entry in plan.queries:
        query, _reason = _normalize_discovery_query(entry.query.strip(), user_question)
        ids = list(dict.fromkeys(item for item in entry.requirement_ids if item in allowed))
        # A route explicitly owned only by optional work has no place in bounded recovery.
        if entry.requirement_ids and not ids and set(entry.requirement_ids).issubset(optional):
            continue
        # Once stable requirements exist, an unbound or invalid route cannot be
        # treated as if it attempted a requirement. Reject it here instead of
        # assigning ownership by position later.
        if allowed and not ids:
            continue
        # Mixed-ownership queries stay eligible until every owned requirement is proven.
        if ids and proven and set(ids) <= proven:
            continue
        key = " ".join(query.split()).casefold()
        existing = keys.get(key)
        if existing is not None:
            ownership[existing] = list(dict.fromkeys([*ownership[existing], *ids]))
            continue
        keys[key] = query
        queries.append(query)
        ownership[query] = ids
    rank_by_id = {
        requirement.requirement_id: _requirement_priority(requirement)
        for requirement in requirements
    }
    original_position = {query: index for index, query in enumerate(queries)}
    queries.sort(
        key=lambda query: (
            min(
                (rank_by_id[item] for item in ownership[query] if item in rank_by_id),
                default=(9, 9),
            ),
            original_position[query],
        )
    )
    if balanced_overview:
        # Complete the principal requested topics before spending discovery slots
        # on separate conditional regimes. Applicability still gates proof review;
        # prioritizing discovery never marks an unverified duty as supported.
        central = [
            q for q in queries if min((rank_by_id[i][0] for i in ownership[q]), default=9) == 1
        ]
        queries = central + [q for q in queries if q not in central]
    return requirements, queries, ownership


def _requirement_priority(requirement: EvidenceRequirement) -> tuple[int, int]:
    """Order material work before applying a bounded query allowance."""
    materiality = {
        "governing_applicability": 0,
        "central_rule": 1,
        "adjacent_rule": 2,
        "secondary_detail": 3,
    }[requirement.materiality]
    return materiality, 0


def _mandatory_partial_ids(
    question: str, requirements: list[EvidenceRequirement]
) -> set[str] | dict[str, set[str]]:
    """Enforce each claim's declared dependencies before the legacy numeric fallback."""
    dependencies = {r.requirement_id: set(r.depends_on) for r in requirements}
    numeric_question = _numeric_rule_question(question)
    if not numeric_question:
        return dependencies if any(dependencies.values()) else set()
    central = [r for r in requirements if r.materiality == "central_rule"]
    governing = {
        r.requirement_id for r in requirements if r.materiality == "governing_applicability"
    }
    if central and any(r.depends_on for r in central):
        for claim in central:
            if not governing.intersection(dependencies[claim.requirement_id]):
                dependencies[claim.requirement_id].add("__governing_applicability_required__")
        return dependencies
    mandatory = {
        r.requirement_id
        for r in requirements
        if r.materiality in {"governing_applicability", "central_rule"}
    }
    # A planner cannot make a focused numeric partial safe by omitting the
    # category/period dependency altogether. The sentinel can never be proven.
    if not any(r.materiality == "governing_applicability" for r in requirements):
        mandatory.add("__governing_applicability_required__")
    if not any(r.materiality == "central_rule" for r in requirements):
        mandatory.add("__central_rule_required__")
    return mandatory


def _numeric_rule_question(question: str) -> bool:
    return bool(
        re.search(
            r"\b(?:rate|threshold|limit|cap|rebate|exemption|allowance|slab|percentage)\b|"
            r"হার|সীমা|রেয়াত|রেয়াত|ছাড়|ছাড়|করমুক্ত",
            question,
            re.IGNORECASE,
        )
    )


def _limit_numeric_partial_scope(
    verdict: CoverageVerdict,
    mandatory_ids: set[str] | dict[str, set[str]],
    question: str,
) -> None:
    """Keep an unresolved subclaim from hiding an independently proven numeric rule."""
    if not _numeric_rule_question(question) or not mandatory_ids or verdict.partial_answer is None:
        return
    fully_proven = {
        check.requirement_id
        for check in verdict.checks
        if check.requirement_id
        and check.supported
        and check.evidence
        and check.fulfillment == "full"
        and not check.unresolved_facets
        and not check.needs_adjacent_context
    }
    original = verdict.partial_answer.requirement_ids
    retained = [requirement_id for requirement_id in original if requirement_id in fully_proven]
    if not retained:
        verdict.partial_answer = None
        return
    verdict.partial_answer.requirement_ids = retained
    excluded = [
        check.description or check.requirement_id
        for check in verdict.checks
        if check.requirement_id in set(original) - set(retained)
    ]
    verdict.partial_answer.exclusions = list(
        dict.fromkeys([*verdict.partial_answer.exclusions, *excluded])
    )[:12]


def _optional_requirement(requirement: EvidenceRequirement, user_question: str) -> bool:
    """Trust the planner's typed origin instead of deleting requirements by words.

    A required consequence can naturally mention a fee or penalty even when the
    user's wording is "consequences".  Keyword subtraction therefore changes the
    request rather than bounding recovery.  Optional corroboration remains bounded
    by its explicit origin; explicit and applicability requirements are retained.
    """
    scope = normalize_request_scope(user_question)
    return requirement.origin == "optional_corroboration" or (
        requirement.task_kind == "personal_eligibility"
        and requirement.origin != "explicit_user_request"
        and not scope.eligibility_requested
        and scope.task_kind != "eligibility"
        and bool(scope.stipulated_facts)
    )


def _authority_dependency_key(record: dict[str, Any]) -> tuple[str, ...]:
    provisions = record.get("target_provisions")
    scoped = (
        tuple(sorted(str(item) for item in provisions if item))
        if isinstance(provisions, list)
        else ()
    )
    return (
        str(record.get("relationship_id") or ""),
        str(record.get("base_revision_id") or ""),
        str(record.get("modifier_revision_id") or ""),
        str(record.get("source_revision_id") or ""),
        str(record.get("outcome") or ""),
        str(record.get("relationship_type") or "modifies"),
        *scoped,
    )


def _chunk_content_hash(chunk: ContextChunk) -> str:
    # Same words under different applicability metadata are not the same proof.
    return content_hash(
        json.dumps(
            {
                "content": chunk.content,
                "scope": asdict(EvidenceScope.from_metadata(chunk.metadata)),
                "revision": chunk.metadata.get("source_revision_id"),
                "authority": chunk.metadata.get("authority_status"),
                "limitations": chunk.metadata.get("authority_limitations"),
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
    )


def _check_confirmed(check: _Check, sources: dict[str, tuple[str, ...]]) -> bool:
    return bool(
        check.supported
        and check.evidence
        and all(
            item.chunk_id in sources and _contains_quote(sources[item.chunk_id], item.quote)
            for item in check.evidence
        )
    )


def _unproven_requested_scope(description: str, proof: str) -> list[str]:
    """Catch concrete requested qualifiers absent from the selected quotation."""

    def normalize(value: str) -> str:
        value = re.sub(r"[\u2010-\u2015]", "-", value.casefold())
        value = value.translate(str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789"))
        value = re.sub(r"\s*-\s*", "-", value)
        return re.sub(
            r"\b(20\d{2})-(\d{2})\b",
            lambda match: f"{match[1]}-{match[1][:2]}{match[2]}",
            value,
        )

    requested_label = re.sub(r"[\u2010-\u2015]", "-", description.casefold())
    requested_label = requested_label.translate(str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789"))
    requested_label = re.sub(r"\s*-\s*", "-", requested_label)
    requested = normalize(description)
    cited = normalize(proof)
    # A publication title or page footer dates the document, not the rate band
    # printed next to it. Keep actual schedule headings in the operative text.
    cited = re.sub(
        r"(?:আয়কর\s*পরিপত্র|income tax circular)\s*20\d{2}-20\d{2}\s*[।.]?\s*\d*",
        "",
        cited,
    )
    gaps: list[str] = []
    periods = re.findall(r"\b20\d{2}-(?:20)?\d{2}\b", requested)
    period_labels = re.findall(r"\b20\d{2}-(?:20)?\d{2}\b", requested_label)
    for period, label in zip(periods, period_labels, strict=True):
        if period not in cited:
            gaps.append(f"requested period {label}")
    other_years = re.findall(r"\b20\d{2}\b", re.sub(r"\b20\d{2}-(?:20)?\d{2}\b", "", requested))
    for year in other_years:
        if year not in cited:
            gaps.append(f"requested year {year}")
    for label, pattern in {
        "private company": r"private compan(?:y|ies)|প্রাইভেট\s*কোম্পান|বেসরকারি\s*কোম্পান",
        "public company": r"public compan(?:y|ies)|পাবলিক\s*কোম্পান",
        "separate treatment": r"separate|পৃথক",
    }.items():
        requested_pattern = r"separate" if label == "separate treatment" else pattern
        if re.search(requested_pattern, requested, re.I) and not re.search(pattern, cited, re.I):
            gaps.append(f"requested scope {label}")
    # Category and conditions are semantic facets reviewed against source quotes.
    # English label presence is not a multilingual completion test.
    return gaps


def _question_scope_for_requirement(description: str, question: str) -> str:
    """Keep a deadline qualifier if the plan names only its broad duty."""
    if not re.search(
        r"\b(?:deadline|window|due date|filing date|assessment year|tax year|period|"
        r"threshold|rate|limit|applicab\w*)\b|করবর্ষ|করহার|সীমা",
        description,
        re.I,
    ):
        return description
    if not re.search(
        r"\b20\d{2}\s*[-\u2013]\s*(?:20)?\d{2}\b|\b(?:deadline|window|due|when)\b",
        question,
        re.I,
    ):
        return description
    qualifiers = re.findall(r"\b20\d{2}\s*[-\u2013]\s*(?:20)?\d{2}\b", question)
    qualifiers.extend(
        label
        for label in ("first-time", "private companies", "public companies", "without income")
        if label in question.casefold()
    )
    return " ".join([description, *(item for item in qualifiers if item not in description)])


def _contradictory_selector_scope(own: _Check, dependency: _Check) -> bool:
    """Reject explicit conflicting years/categories even when provenance agrees."""
    own_text = " ".join(item.quote for item in own.evidence)
    dependency_text = " ".join(item.quote for item in dependency.evidence)
    own_periods = {
        (period.kind, period.start_year, period.end_year)
        for period in normalize_request_scope(own_text).requested_periods
    }
    dependency_periods = {
        (period.kind, period.start_year, period.end_year)
        for period in normalize_request_scope(dependency_text).requested_periods
    }
    if own_periods and dependency_periods and not own_periods.issubset(dependency_periods):
        return True
    categories = (
        r"ordinary individual",
        r"female individual",
        r"disabled individual",
        r"private compan(?:y|ies)",
        r"public compan(?:y|ies)",
    )
    own_categories = {
        index for index, pattern in enumerate(categories) if re.search(pattern, own_text, re.I)
    }
    dependency_categories = {
        index
        for index, pattern in enumerate(categories)
        if re.search(pattern, dependency_text, re.I)
    }
    return bool(
        own_categories
        and dependency_categories
        and not own_categories.issubset(dependency_categories)
    )


def _same_governing_span(chunk: ContextChunk, own: _Check, dependency: _Check) -> bool:
    """Bind heading dependencies to selected spans, never just a shared chunk UUID."""
    lines = chunk.content.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    def bounds(quote: Any) -> tuple[int, int] | None:
        selected = quote._resolved_range
        if selected:
            first, last = selected
            if first < 1 or last > len(lines):
                return None
            if "".join(lines[first - 1 : last]) != quote.quote:
                return None
            return first - 1, last
        positions = [match.start() for match in re.finditer(re.escape(quote.quote), chunk.content)]
        if len(positions) != 1 or not quote.quote:
            return None
        start, end = positions[0], positions[0] + len(quote.quote)
        first = max(index for index, value in enumerate(offsets[:-1]) if value <= start)
        last = next((index for index, value in enumerate(offsets) if value >= end), len(lines))
        return first, last

    marker = re.compile(
        r"^\s*(?:#{1,6}\s|table\s+[A-Za-z0-9]|schedule\s+[A-Za-z0-9]|"
        r"(?:AY|FY|assessment year|fiscal year)\b|"
        r"(?:ordinary|resident|nonresident|private|public|female|disabled|company|companies)"
        r"\b|section\s+\d|ধারা\s*[\u09E6-\u09EF0-9]+|নারী|মহিলা|প্রতিবন্ধী)|"
        r"^[\u09E6-\u09EF0-9]{4}\s*[-\u2013]\s*[\u09E6-\u09EF0-9]{2,4}.*(?:করবর্ষ|করহার)",
        re.I,
    )
    for selected in own.evidence:
        if selected.chunk_id != str(chunk.chunk_id):
            continue
        target = bounds(selected)
        if target is None:
            return False
        for heading in dependency.evidence:
            if heading.chunk_id != selected.chunk_id:
                continue
            source = bounds(heading)
            if source is None or source[0] > target[0]:
                return False
            if any(marker.search(line) for line in lines[source[1] : target[0] + 1]):
                return False
            own_periods = normalize_request_scope(selected.quote).requested_periods
            dep_periods = normalize_request_scope(heading.quote).requested_periods
            if (
                own_periods
                and dep_periods
                and not {
                    (period.kind, period.start_year, period.end_year) for period in own_periods
                }.issubset(
                    {(period.kind, period.start_year, period.end_year) for period in dep_periods}
                )
            ):
                return False
    return True


def _guard_review_fulfillment(
    review: CoverageVerdict | CoverageDelta,
    requirements: list[EvidenceRequirement],
    context: list[ContextChunk],
    question: str = "",
) -> CoverageVerdict | CoverageDelta:
    """A generic quote cannot certify a specifically scoped requirement."""
    descriptions = {item.requirement_id: item.description for item in requirements}
    sources = {str(chunk.chunk_id): _quote_tokens(chunk.content) for chunk in context}
    chunks_by_id = {str(chunk.chunk_id): chunk for chunk in context}
    provenance: dict[str, set[tuple[str, ...]]] = {}
    for chunk in context:
        provenance.setdefault(str(chunk.chunk_id), set()).add(
            tuple(
                str(chunk.metadata.get(key) or "")
                for key in (
                    "project_id",
                    "source_revision_id",
                    "index_build_id",
                    "source_metadata_generation",
                    "table_id",
                )
            )
        )
    ambiguous_ids = {key for key, identities in provenance.items() if len(identities) > 1}
    by_id = {item.requirement_id: item for item in requirements}
    checks_by_id = {check.requirement_id: check for check in review.checks}
    resolved: dict[str, _Check] = {}
    active: set[str] = set()

    def same_boundary(own: _Check, dependency: _Check) -> bool:
        if _contradictory_selector_scope(own, dependency):
            return False
        own_chunks = [chunks_by_id.get(item.chunk_id) for item in own.evidence]
        dep_chunks = [chunks_by_id.get(item.chunk_id) for item in dependency.evidence]
        for left in own_chunks:
            for right in dep_chunks:
                if left is None or right is None:
                    return False
                if left.chunk_id == right.chunk_id:
                    if not _same_governing_span(left, own, dependency):
                        return False
                    continue
                keys = (
                    "project_id",
                    "source_revision_id",
                    "index_build_id",
                    "source_metadata_generation",
                    "table_id",
                )
                if left.document_id != right.document_id or any(
                    not left.metadata.get(key) or left.metadata.get(key) != right.metadata.get(key)
                    for key in keys
                ):
                    return False
        return bool(own_chunks and dep_chunks)

    def compose(identity: str) -> _Check | None:
        if identity in active:
            return None
        if identity in resolved:
            return resolved[identity]
        check = checks_by_id.get(identity)
        requirement = by_id.get(identity)
        if check is None or requirement is None:
            return None
        active.add(identity)
        evidence = list(check.evidence)
        if any(item.chunk_id in ambiguous_ids for item in evidence):
            check = check.model_copy(
                update={
                    "supported": False,
                    "fulfillment": "none",
                    "unresolved_facets": [
                        *check.unresolved_facets,
                        "conflicting source provenance",
                    ],
                }
            )
        for dependency_id in requirement.depends_on:
            dependency = compose(dependency_id)
            if (
                dependency is None
                or dependency.fulfillment != "full"
                or dependency.unresolved_facets
                or dependency.needs_adjacent_context
                or not _check_confirmed(dependency, sources)
                or not same_boundary(check, dependency)
            ):
                check = check.model_copy(
                    update={
                        "fulfillment": "partial",
                        "unresolved_facets": list(
                            dict.fromkeys(
                                [
                                    *check.unresolved_facets,
                                    f"unproven governing dependency {dependency_id}",
                                ]
                            )
                        ),
                    }
                )
                continue
            for item in dependency.evidence:
                if not any(
                    old.chunk_id == item.chunk_id and old.quote == item.quote for old in evidence
                ):
                    evidence.append(item)
        if len(evidence) <= 8:
            check = check.model_copy(update={"evidence": evidence})
        proof = " ".join(
            item.quote
            for item in check.evidence
            if item.chunk_id in sources and _contains_quote(sources[item.chunk_id], item.quote)
        )
        gaps = _unproven_requested_scope(
            _question_scope_for_requirement(requirement.description, question), proof
        )
        gaps = [
            gap
            for gap in gaps
            if not (
                gap.startswith("requested period ")
                and any(
                    gap.removeprefix("requested period ") in old for old in check.unresolved_facets
                )
            )
        ]
        if gaps:
            check = check.model_copy(
                update={
                    "fulfillment": "partial",
                    "needs_adjacent_context": check.needs_adjacent_context
                    or (
                        _numeric_rule_question(requirement.description)
                        and any(
                            gap.startswith(("requested period", "requested year")) for gap in gaps
                        )
                    ),
                    "unresolved_facets": list(dict.fromkeys([*check.unresolved_facets, *gaps])),
                }
            )
        active.remove(identity)
        resolved[identity] = check
        return check

    composed = [
        compose(check.requirement_id) or check if check.requirement_id else check
        for check in review.checks
    ]
    review = review.model_copy(update={"checks": composed})
    changed = False
    checks: list[_Check] = []
    missing = list(review.missing)
    for check in review.checks:
        description = descriptions.get(check.requirement_id or "")
        if check.fulfillment != "full" or not description:
            checks.append(check)
            original = checks_by_id.get(check.requirement_id)
            if (
                description
                and original is not None
                and original.fulfillment == "full"
                and check.fulfillment != "full"
            ):
                changed = True
                if description not in missing:
                    missing.append(description)
            continue
        proof = " ".join(
            item.quote
            for item in check.evidence
            if item.chunk_id in sources and _contains_quote(sources[item.chunk_id], item.quote)
        )
        scope_gaps = _unproven_requested_scope(
            _question_scope_for_requirement(description, question), proof
        )
        scope_gaps = [
            gap
            for gap in scope_gaps
            if not (
                gap.startswith("requested period ")
                and any(
                    gap.removeprefix("requested period ") in old for old in check.unresolved_facets
                )
            )
        ]
        if re.search(r"\bconditional\b|শর্ত", description, re.I) and (
            not check.condition_facets
            or any(
                any(index < 0 or index >= len(check.evidence) for index in facet.evidence_indexes)
                for facet in check.condition_facets
            )
        ):
            scope_gaps.append("missing source-attested condition facets")
        if not scope_gaps and not check.unresolved_facets:
            checks.append(check)
            continue
        changed = True
        checks.append(
            check.model_copy(
                update={
                    "fulfillment": "partial",
                    "unresolved_facets": list(
                        dict.fromkeys([*check.unresolved_facets, *scope_gaps])
                    ),
                    "needs_adjacent_context": check.needs_adjacent_context
                    or (
                        bool(
                            re.search(r"threshold|rate|limit|schedule|করহার|সীমা", description, re.I)
                        )
                        and any(gap.startswith("requested period ") for gap in scope_gaps)
                    ),
                }
            )
        )
        if description not in missing:
            missing.append(description)
    if not changed:
        return review
    return review.model_copy(
        update={
            "complete": False,
            "missing": missing,
            "gap_kinds": ["source_rule"] * len(missing),
            "checks": checks,
        }
    )


@dataclass
class _ProofFacet:
    requirement_id: str
    description: str
    origin: str
    check: _Check | None = None
    evidence_ids: tuple[str, ...] = ()
    evidence_hashes: tuple[str, ...] = ()
    authority_keys: tuple[tuple[str, ...], ...] = ()
    valid: bool = False

    @property
    def fulfilled(self) -> bool:
        """Exact source support is retained even when the duty is only partly met."""
        return (
            self.valid
            and self.check is not None
            and self.check.fulfillment == "full"
            and not self.check.unresolved_facets
        )


def _candidate_only_source_gap(gap: str) -> bool:
    """Identify an objection to the earlier selected passage, not a missing duty."""
    normalized = " ".join(gap.casefold().split())
    return bool(
        re.search(r"\b(?:admitted|retrieved|selected|supplied)\b", normalized)
        and re.search(
            r"\b(?:not proof|does not establish|cannot establish|not established by)\b",
            normalized,
        )
    )


class _TurnProofMap:
    """Turn-local validated checks keyed by canonical requirement IDs."""

    def __init__(self, requirements: list[EvidenceRequirement]) -> None:
        self.requirements = list(requirements)
        self._facets: dict[str, _ProofFacet] = {
            item.requirement_id: _ProofFacet(
                requirement_id=item.requirement_id,
                description=item.description,
                origin=item.origin,
            )
            for item in requirements
        }
        self._known_authority_keys: set[tuple[str, ...]] = set()
        self._retained_gaps: list[str] = []
        self._gap_owners: dict[str, set[str]] = {}
        self._validated_partial: PartialAnswerScope | None = None

    def canonical_ids(self) -> set[str]:
        return set(self._facets)

    def proven_ids(self) -> set[str]:
        return {key for key, facet in self._facets.items() if facet.fulfilled}

    def unresolved_ids(self) -> set[str]:
        return {key for key, facet in self._facets.items() if not facet.fulfilled}

    def remember_records(self, records: list[dict[str, Any]]) -> None:
        for record in records:
            self._known_authority_keys.add(_authority_dependency_key(record))

    def remember_gaps(self, missing: list[str], checks: list[_Check] | None = None) -> None:
        for check in checks or []:
            if check.requirement_id:
                for gap in check.unresolved_facets:
                    self._gap_owners.setdefault(gap, set()).add(check.requirement_id)
        for item in missing:
            text = str(item).strip()
            if text and text not in self._retained_gaps:
                self._retained_gaps.append(text)
            exact_owners = {
                identity
                for identity, facet in self._facets.items()
                if text in {identity, facet.description}
            }
            if len(self._facets) == 1 and _candidate_only_source_gap(text):
                exact_owners.update(self._facets)
            self._gap_owners.setdefault(text, set()).update(exact_owners)

    def remember_partial(self, partial: PartialAnswerScope | None) -> None:
        if partial is not None:
            self._validated_partial = partial

    def _gap_resolved(self, label: str) -> bool:
        normalized = " ".join(label.casefold().split())
        for facet in self._facets.values():
            if not facet.fulfilled:
                continue
            identities = {facet.requirement_id, facet.description or facet.requirement_id}
            if normalized in {" ".join(item.casefold().split()) for item in identities}:
                return True
        return False

    def _reconciled_gaps(self, reported: list[str]) -> list[str]:
        """Merge canonical, retained, and newly reported gaps without omission closure."""
        unresolved = [
            self._facets[item.requirement_id].description or item.requirement_id
            for item in self.requirements
            if not self._facets[item.requirement_id].fulfilled
        ]
        missing: list[str] = []
        for raw in [*unresolved, *self._retained_gaps, *reported]:
            label = str(raw).strip()
            if not label or label in missing or self._gap_resolved(label):
                continue
            missing.append(label)
        return missing

    def new_affecting_records(
        self, records: list[dict[str, Any]], chunks: list[ContextChunk]
    ) -> list[dict[str, Any]]:
        fresh: list[dict[str, Any]] = []
        seen: set[tuple[str, ...]] = set()
        for record in records:
            key = _authority_dependency_key(record)
            if key in self._known_authority_keys or key in seen:
                continue
            seen.add(key)
            if any(authority_record_affects_chunk(record, chunk) for chunk in chunks):
                fresh.append(record)
        return fresh

    def invalidate_changed(
        self,
        budgeted: list[ContextChunk],
        records: list[dict[str, Any]],
        discovered: list[ContextChunk] | None = None,
    ) -> set[str]:
        by_id = {str(chunk.chunk_id): chunk for chunk in budgeted}
        for chunk in discovered or []:
            current = by_id.get(str(chunk.chunk_id))
            if current is None or _chunk_content_hash(current) != _chunk_content_hash(chunk):
                by_id[str(chunk.chunk_id)] = chunk
        fresh_records = self.new_affecting_records(records, list(by_id.values()))
        changed: set[str] = set()
        revisit: set[str] = set()
        for req_id, facet in self._facets.items():
            if not facet.valid:
                changed.add(req_id)
                continue
            missing_or_changed = False
            for evidence_id, evidence_hash in zip(
                facet.evidence_ids, facet.evidence_hashes, strict=False
            ):
                current = by_id.get(evidence_id)
                if current is None or _chunk_content_hash(current) != evidence_hash:
                    missing_or_changed = True
                    break
            if missing_or_changed:
                changed.add(req_id)
                continue
            proof_chunks = [by_id[item] for item in facet.evidence_ids if item in by_id]
            if any(
                authority_record_affects_chunk(record, chunk)
                for record in fresh_records
                for chunk in proof_chunks
            ):
                changed.add(req_id)
                continue
            if not facet.fulfilled:
                revisit.add(req_id)
        dependents = set(changed)
        changed_evidence = {
            evidence_id for req_id in changed for evidence_id in self._facets[req_id].evidence_ids
        }
        changed_authority = {
            key for req_id in changed for key in self._facets[req_id].authority_keys
        }
        for req_id, facet in self._facets.items():
            if req_id in dependents or not facet.valid:
                continue
            if changed_evidence & set(facet.evidence_ids) or changed_authority & set(
                facet.authority_keys
            ):
                dependents.add(req_id)
        # Propagate explicit claim dependencies as well as shared source spans.
        while True:
            additions = {
                item.requirement_id
                for item in self.requirements
                if set(item.depends_on) & dependents
            } - dependents
            if not additions:
                break
            dependents.update(additions)
        for req_id in dependents:
            self._mark_invalid(req_id)
        return dependents | revisit

    def _mark_invalid(self, req_id: str) -> None:
        facet = self._facets[req_id]
        facet.valid = False
        if facet.check is not None and facet.check.supported:
            facet.check = facet.check.model_copy(update={"supported": False})

    def _retain_canonical_check(self, check: _Check) -> _Check:
        """Keep the original obligation text; a later review cannot rename it."""
        req_id = check.requirement_id
        if not req_id:
            return check
        facet = self._facets[req_id]
        canonical = facet.description.strip()
        if not canonical and check.description.strip():
            facet.description = check.description
            return check
        if canonical and check.description != canonical:
            return check.model_copy(update={"description": canonical})
        return check

    def accept_check(
        self, check: _Check, chunks: list[ContextChunk], records: list[dict[str, Any]]
    ) -> None:
        req_id = check.requirement_id
        if not req_id or req_id not in self._facets:
            return
        check = self._retain_canonical_check(check)
        facet = self._facets[req_id]
        facet.check = check
        evidence_ids = tuple(item.chunk_id for item in check.evidence)
        by_id = {str(chunk.chunk_id): chunk for chunk in chunks}
        hashes = tuple(_chunk_content_hash(by_id[item]) for item in evidence_ids if item in by_id)
        authority: list[tuple[str, ...]] = []
        for evidence_id in evidence_ids:
            chunk = by_id.get(evidence_id)
            if chunk is None:
                continue
            for record in records:
                if authority_record_affects_chunk(record, chunk):
                    authority.append(_authority_dependency_key(record))
        facet.evidence_ids = evidence_ids
        facet.evidence_hashes = hashes
        facet.authority_keys = tuple(dict.fromkeys(authority))
        sources = {str(chunk.chunk_id): _quote_tokens(chunk.content) for chunk in chunks}
        facet.valid = _check_confirmed(check, sources)

    def accept_confirmed(
        self,
        checks: list[_Check],
        chunks: list[ContextChunk],
        records: list[dict[str, Any]],
    ) -> None:
        sources = {str(chunk.chunk_id): _quote_tokens(chunk.content) for chunk in chunks}
        for check in checks:
            if check.requirement_id in self._facets and _check_confirmed(check, sources):
                self.accept_check(check, chunks, records)

    def observe_review_checks(
        self,
        checks: list[_Check],
        chunks: list[ContextChunk],
        records: list[dict[str, Any]],
    ) -> None:
        sources = {str(chunk.chunk_id): _quote_tokens(chunk.content) for chunk in chunks}
        for check in checks:
            req_id = check.requirement_id
            if not req_id or req_id not in self._facets:
                continue
            if _check_confirmed(check, sources):
                self.accept_check(check, chunks, records)
                continue
            check = self._retain_canonical_check(check)
            facet = self._facets[req_id]
            facet.check = check
            facet.valid = False

    def merge_delta(
        self,
        delta: CoverageDelta | CoverageVerdict,
        reviewing: set[str],
        chunks: list[ContextChunk],
        records: list[dict[str, Any]],
    ) -> CoverageVerdict:
        pending = set(reviewing)
        prior_proof_ids = {
            item: self._facets[item].evidence_ids for item in pending if item in self._facets
        }
        sources = {str(chunk.chunk_id): _quote_tokens(chunk.content) for chunk in chunks}
        for check in delta.checks:
            req_id = check.requirement_id
            if not req_id or req_id not in self._facets or req_id in pending:
                continue
            if self._facets[req_id].valid and not _check_confirmed(check, sources):
                pending.add(req_id)
        self.observe_review_checks(
            [check for check in delta.checks if check.requirement_id in pending],
            chunks,
            records,
        )
        returned = {check.requirement_id for check in delta.checks}
        for req_id in pending:
            if req_id not in returned:
                self._mark_invalid(req_id)
        # A replacement witness closes only explicitly identified gaps of this
        # reviewed requirement. Omitted or unrelated gaps remain unresolved.
        closures = {
            gap
            for check in delta.checks
            if check.requirement_id in pending
            and self._facets[check.requirement_id].fulfilled
            and self._facets[check.requirement_id].evidence_ids
            != prior_proof_ids.get(check.requirement_id)
            for gap in check.resolved_gaps
            if self._gap_owners.get(gap) == {check.requirement_id}
            and not _unproven_requested_scope(
                gap, " ".join(quote.quote for quote in check.evidence)
            )
        }
        self._retained_gaps = [gap for gap in self._retained_gaps if gap not in closures]
        self.remember_gaps(list(delta.missing), delta.checks)
        missing = self._reconciled_gaps(list(delta.missing))
        partial = delta.partial_answer
        if partial is not None and missing:
            exclusions = list(partial.exclusions)
            for label in missing:
                if label in exclusions:
                    continue
                exclusions.append(label)
            if exclusions != list(partial.exclusions):
                partial = partial.model_copy(update={"exclusions": exclusions})
        self.remember_gaps(missing)
        if partial is not None:
            self.remember_partial(partial)
        return self.canonical_verdict(
            missing=missing,
            gap_kinds=list(delta.gap_kinds),
            partial_answer=partial,
        )

    def canonical_verdict(
        self,
        *,
        missing: list[str],
        gap_kinds: list[str],
        partial_answer: PartialAnswerScope | None,
    ) -> CoverageVerdict:
        checks: list[_Check] = []
        for requirement in self.requirements:
            facet = self._facets[requirement.requirement_id]
            if facet.check is not None:
                check = facet.check
                if not facet.valid and check.supported:
                    check = check.model_copy(update={"supported": False})
                checks.append(check)
            else:
                checks.append(
                    _Check(
                        requirement_id=requirement.requirement_id,
                        description=facet.description,
                        supported=False,
                        evidence=[],
                    )
                )
        missing = self._reconciled_gaps(missing)
        complete = (
            bool(checks)
            and all(check.supported and check.evidence for check in checks)
            and not missing
            and not self.unresolved_ids()
        )
        kinds: list[Any] = gap_kinds if not complete else []
        if kinds and len(kinds) != len(missing if not complete else []):
            kinds = ["source_rule"] * len(missing)
        return CoverageVerdict(
            complete=complete,
            missing=[] if complete else missing,
            gap_kinds=kinds,
            partial_answer=None if complete else partial_answer,
            checks=checks,
        )

    def snapshot_verdict(self) -> CoverageVerdict:
        """Canonical verdict from retained proof; extra reviewer gaps stay incomplete."""
        missing = self._reconciled_gaps([])
        partial = None if not missing else self._validated_partial
        return self.canonical_verdict(
            missing=missing,
            gap_kinds=["source_rule"] * len(missing),
            partial_answer=partial,
        )

    def relevant_chunks(
        self, budgeted: list[ContextChunk], reviewing: set[str]
    ) -> list[ContextChunk]:
        needed: set[str] = set()
        for req_id in reviewing:
            needed.update(self._facets[req_id].evidence_ids)
        for facet in self._facets.values():
            if facet.valid:
                needed.update(facet.evidence_ids)
        selected = [chunk for chunk in budgeted if str(chunk.chunk_id) in needed]
        if selected:
            return selected
        return budgeted

    def diagnostics(self) -> dict[str, Any]:
        return {
            "unresolved_gap_owners": {
                gap: sorted(self._gap_owners.get(gap, set())) for gap in self._retained_gaps
            },
            "facets": [
                {
                    "requirement_id": facet.requirement_id,
                    "description": facet.description,
                    "origin": facet.origin,
                    "valid": facet.valid,  # Legacy field: evidence retention only.
                    "evidence_valid": facet.valid,
                    "requirement_fulfilled": facet.fulfilled,
                    "evidence_ids": list(facet.evidence_ids),
                    "evidence_hashes": list(facet.evidence_hashes),
                    "authority_keys": [list(key) for key in facet.authority_keys],
                }
                for facet in self._facets.values()
            ],
        }


def _requirement_labels_match(left: str, right: str) -> bool:
    def tokens(value: str) -> set[str]:
        return set(re.findall(r"[^\W_]+", value.casefold(), re.UNICODE))

    left_tokens, right_tokens = tokens(left), tokens(right)
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens)) >= 0.6


def _source_line_records(content: str) -> list[dict[str, Any]]:
    """Copyable selectors retain original positions; blank lines cannot prove a fact."""
    return [
        {"start_line": number, "end_line": number, "text": line}
        for number, line in enumerate(content.splitlines(), 1)
        if line.strip()
    ]


def _review_evidence_key(
    chunks: list[ContextChunk],
    records: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    unresolved_ids: list[str] | None = None,
) -> str:
    """Compare exact proof inputs, never similarity, rank, or just chunk IDs."""

    def canonical(value: Any) -> str:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)

    relevant_authority = sorted(
        {
            canonical(_authority_dependency_key(record))
            for record in records
            if any(authority_record_affects_chunk(record, chunk) for chunk in chunks)
        }
    )
    return canonical(
        {
            "requirements": requirements,
            "unresolved": sorted(unresolved_ids or []),
            "authority": relevant_authority,
            "passages": sorted(
                {
                    canonical(
                        {
                            "id": chunk.chunk_id,
                            "document": chunk.document_id,
                            "hash": _chunk_content_hash(chunk),
                            "revision": chunk.metadata.get("source_revision_id"),
                            "effective_from": chunk.metadata.get("source_effective_from"),
                            "effective_to": chunk.metadata.get("source_effective_to"),
                            "lifecycle": chunk.metadata.get("source_lifecycle_status"),
                            "authority_status": chunk.metadata.get("authority_status"),
                        }
                    )
                    for chunk in chunks
                }
            ),
        }
    )


def _source_hints(
    chunks: list[ContextChunk], *, include_work_metadata: bool = False
) -> list[dict[str, Any]]:
    """A repeated high-ranking source must not hide the other corpus languages."""
    seen: set[object] = set()
    candidates: list[dict[str, Any]] = []
    for chunk in chunks:
        if chunk.document_id in seen:
            continue
        seen.add(chunk.document_id)
        candidates.append(
            {
                "title": chunk.filename,
                "source": {
                    "language": chunk.metadata.get("chunk_language")
                    or chunk.metadata.get("document_language")
                    or chunk.metadata.get("language"),
                    **{
                        key: chunk.metadata[key]
                        for key in (
                            _SOURCE_CONTEXT_KEYS
                            if include_work_metadata
                            else _AUTHORITATIVE_SOURCE_CONTEXT_KEYS
                        )
                        if key in chunk.metadata
                        and key not in {"authority_limitations", "language"}
                    },
                },
            }
        )
    # Reserve one slot per observed language before filling by retrieval rank.
    # Otherwise six distinct English works can hide a seventh Bangla work.
    first_by_language: dict[str, dict[str, Any]] = {}
    for hint in candidates:
        language = str(hint["source"].get("language") or "unknown")
        first_by_language.setdefault(language, hint)
    reserved = list(first_by_language.values())[:6]
    reserved_objects = {id(hint) for hint in reserved}
    return [*reserved, *(hint for hint in candidates if id(hint) not in reserved_objects)][:6]


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
    languages = sorted(
        {
            str(h["source"]["language"])
            for h in hints
            if h["source"].get("language") in DEFAULT_SUPPORTED_TARGET_LANGUAGES
        }
    )
    if not languages:
        return ""
    return (
        f"\nRetrieved source languages: {', '.join(languages)}. "
        "Write the query concepts in the language of the relevant source, even when the user "
        "asks in a different language or the document title is translated. "
        "Choose one short source-language query per distinct necessary concept, "
        "using the source relevant to that concept. Recency alone does not make "
        "a source govern a different subject or establish the language of that subject. "
        "Preserve explicitly requested Act or work names when they distinguish the topic; "
        "omit unrelated publication boilerplate. Use another language only when it adds "
        "discovery value within the configured query allowance.\n"
    )


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
    answerable_scope: dict[str, Any] | None = None


def _add_usage(left: ChatUsage | None, right: ChatUsage | None) -> ChatUsage:
    return ChatUsage(
        input_tokens=left.input_tokens + right.input_tokens
        if left and right and left.input_tokens is not None and right.input_tokens is not None
        else None,
        output_tokens=left.output_tokens + right.output_tokens
        if left and right and left.output_tokens is not None and right.output_tokens is not None
        else None,
    )


_PURPOSE_COUNTERS = {
    "recovery_planning": "planner_calls",
    "coverage_review": "coverage_review_calls",
}


class _SelectorReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str | None = Field(default=None, min_length=1, max_length=80)
    query_index: int = -1
    evidence_index: int = Field(ge=0, le=7)
    chunk_id: str = Field(min_length=1, max_length=80)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class _SelectorRepairResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replacements: list[_SelectorReplacement] = Field(min_length=1, max_length=16)


def _proof_from_parsed(parsed: BaseModel) -> Any:
    return getattr(parsed, "coverage", parsed)


def _proof_checks(parsed: BaseModel) -> list[_Check]:
    proof = _proof_from_parsed(parsed)
    if proof is None or isinstance(proof, dict):
        return []
    checks = getattr(proof, "checks", None)
    return list(checks) if checks else []


def _alias_maps(
    source_ids: dict[str, str] | None, context: list[ContextChunk] | None
) -> tuple[dict[str, str], set[str]]:
    aliases: dict[str, str] = {}
    known: set[str] = set()
    for label, identifier in (source_ids or {}).items():
        aliases[label] = identifier
        aliases[label.casefold()] = identifier
        known.add(identifier)
        known.add(label)
        known.add(label.casefold())
    for chunk in context or []:
        known.add(str(chunk.chunk_id))
    return aliases, known


def _canonicalize_known_selectors(
    parsed: BaseModel,
    source_ids: dict[str, str] | None,
    context: list[ContextChunk] | None,
) -> bool:
    aliases, _known = _alias_maps(source_ids, context)
    changed = False
    for check in _proof_checks(parsed):
        for item in check.evidence:
            mapped = aliases.get(item.chunk_id) or aliases.get(item.chunk_id.casefold())
            if mapped and mapped != item.chunk_id:
                item.chunk_id = mapped
                changed = True
    return changed


def _selector_range_failures(
    parsed: BaseModel,
    context: list[ContextChunk] | None,
    source_ids: dict[str, str] | None,
) -> list[dict[str, Any]]:
    if context is None:
        return []
    lines_by_id = {str(chunk.chunk_id): chunk.content.splitlines() for chunk in context}
    aliases, known = _alias_maps(source_ids, context)
    failures: list[dict[str, Any]] = []
    for check_index, check in enumerate(_proof_checks(parsed)):
        for evidence_index, item in enumerate(check.evidence):
            if item.start_line is None:
                continue
            identifier = aliases.get(item.chunk_id) or aliases.get(item.chunk_id.casefold())
            identifier = identifier or item.chunk_id
            lines = lines_by_id.get(identifier, [])
            end = item.end_line or item.start_line
            if end > len(lines) or not "".join(lines[item.start_line - 1 : end]).strip():
                failures.append(
                    {
                        "requirement_id": check.requirement_id,
                        "query_index": check.query_index,
                        "evidence_index": evidence_index,
                        "check_index": check_index,
                        "chunk_id": item.chunk_id,
                        "source_id": item.chunk_id,
                        "source_known": identifier in lines_by_id or item.chunk_id in known,
                        "line_count": len(lines),
                        "start_line": item.start_line,
                        "end_line": end,
                        "nonempty_lines": [
                            number for number, line in enumerate(lines, start=1) if line.strip()
                        ],
                    }
                )
    return failures


def _selector_failure_diagnostics(failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist bounded selector facts without source text or provider output."""
    items: list[dict[str, Any]] = []
    for failure in failures[:16]:
        line_count = int(failure.get("line_count") or 0)
        start = int(failure.get("start_line") or 0)
        end = int(failure.get("end_line") or start)
        if not failure.get("source_known"):
            reason = "unknown_source"
        elif start < 1 or end < start or end > line_count:
            reason = "out_of_range"
        else:
            reason = "blank_range"
        items.append(
            {
                "requirement_id": failure.get("requirement_id"),
                "query_index": failure.get("query_index"),
                "evidence_index": failure.get("evidence_index"),
                "source_known": bool(failure.get("source_known")),
                "line_count": line_count,
                "start_line": start,
                "end_line": end,
                "reason": reason,
            }
        )
    return items


def _selector_repair_messages(
    parsed: BaseModel,
    failures: list[dict[str, Any]],
    context: list[ContextChunk],
    source_ids: dict[str, str] | None,
) -> list[ChatMessage] | None:
    """Build a small repair request containing only failed checks and their sources."""
    if not failures or any(not failure.get("source_known") for failure in failures):
        return None
    aliases, _known = _alias_maps(source_ids, context)
    chunks = {str(chunk.chunk_id): chunk for chunk in context}
    checks = _proof_checks(parsed)
    requested_checks: list[dict[str, Any]] = []
    requested_sources: dict[str, dict[str, Any]] = {}
    for failure in failures:
        check_index = int(failure["check_index"])
        if check_index >= len(checks):
            return None
        check = checks[check_index]
        identifier = aliases.get(str(failure["chunk_id"])) or aliases.get(
            str(failure["chunk_id"]).casefold()
        )
        identifier = identifier or str(failure["chunk_id"])
        chunk = chunks.get(identifier)
        if chunk is None:
            return None
        requested_sources.setdefault(
            identifier,
            {
                "chunk_id": identifier,
                "source_lines": _source_line_records(chunk.content),
            },
        )
        requested_checks.append(
            {
                "requirement_id": check.requirement_id,
                "query_index": check.query_index,
                "description": check.description,
                "supported": check.supported,
                "evidence_index": failure["evidence_index"],
                "invalid_selector": {
                    "chunk_id": identifier,
                    "start_line": failure["start_line"],
                    "end_line": failure["end_line"],
                },
            }
        )
    payload = {
        "task": "replace_invalid_evidence_selectors",
        "failed_checks": requested_checks,
        "sources": list(requested_sources.values()),
    }
    return [
        ChatMessage(
            role=ChatRole.SYSTEM,
            content=(
                "Return only JSON matching this schema: "
                + json.dumps(_SelectorRepairResponse.model_json_schema())
                + " Replace every listed invalid selector exactly once. Copy chunk_id, "
                "start_line and end_line from the supplied source_lines. Preserve each "
                "requirement_id, query_index and evidence_index. Do not change whether a "
                "check is supported and do not invent or infer missing text."
            ),
        ),
        ChatMessage(role=ChatRole.USER, content=json.dumps(payload, ensure_ascii=False)),
    ]


def _is_contradictory_completion(exc: ValidationError) -> bool:
    return any(
        str(error.get("type") or "")
        in {"coverage_inconsistent_completion", "coverage_unreviewed_missing_inputs"}
        for error in exc.errors(include_input=False)
    )


def _apply_selector_replacements(
    parsed: BaseModel, repair: _SelectorRepairResponse, failures: list[dict[str, Any]]
) -> bool:
    working = parsed.model_copy(deep=True)
    checks = _proof_checks(working)
    allowed = {
        (
            item.get("requirement_id"),
            item.get("query_index"),
            item.get("evidence_index"),
        )
        for item in failures
    }
    applied = False
    for replacement in repair.replacements:
        key = (replacement.requirement_id, replacement.query_index, replacement.evidence_index)
        loose_key = (replacement.requirement_id, -1, replacement.evidence_index)
        if key not in allowed and loose_key not in allowed:
            return False
        if replacement.requirement_id is not None:
            matches = [item for item in checks if item.requirement_id == replacement.requirement_id]
        else:
            matches = [
                item
                for item in checks
                if item.requirement_id is None and item.query_index == replacement.query_index
            ]
        if len(matches) != 1:
            return False
        check = matches[0]
        if replacement.evidence_index >= len(check.evidence):
            return False
        target = check.evidence[replacement.evidence_index]
        target.chunk_id = replacement.chunk_id
        target.start_line = replacement.start_line
        target.end_line = replacement.end_line
        target.quote = ""
        applied = True
    if not applied:
        return False
    original_checks = _proof_checks(parsed)
    updated_checks = _proof_checks(working)
    if len(original_checks) != len(updated_checks):
        return False
    for original, updated in zip(original_checks, updated_checks, strict=True):
        original.evidence = updated.evidence
    return True


def _request_work(llm: BaseLLMProvider) -> RequestWork | None:
    work = getattr(llm, "work", None)
    if isinstance(work, RequestWork):
        return work
    return current_request_work()


def _review_protocol_issues(
    parsed: BaseModel,
    expected_requirement_ids: set[str] | None,
    require_fulfillment: bool,
) -> list[str]:
    issues: list[str] = []
    if expected_requirement_ids is not None and isinstance(parsed, CoverageVerdict):
        actual = [check.requirement_id for check in parsed.checks]
        missing = expected_requirement_ids - set(actual)
        foreign = {str(identity) for identity in set(actual) - expected_requirement_ids}
        duplicates = {str(identity) for identity in actual if actual.count(identity) > 1}
        if missing or foreign or duplicates:
            issues.append(
                f"review identity protocol error: missing={sorted(missing)}, "
                f"foreign={sorted(foreign)}, duplicate={sorted(duplicates)}"
            )
    if require_fulfillment and isinstance(parsed, (CoverageVerdict, CoverageDelta)):
        unmarked = [
            check.requirement_id or f"query-{check.query_index}"
            for check in parsed.checks
            if check.fulfillment is None
        ]
        if unmarked:
            issues.append(f"missing explicit fulfillment: {unmarked}")
    return issues


def _mark_unfulfilled_checks_incomplete(verdict: CoverageVerdict) -> CoverageVerdict:
    unmarked = [
        check.requirement_id or f"query-{check.query_index}"
        for check in verdict.checks
        if check.fulfillment is None
    ]
    if not unmarked:
        return verdict
    checks = [
        check.model_copy(update={"supported": False, "fulfillment": "none"})
        if check.fulfillment is None
        else check
        for check in verdict.checks
    ]
    gaps = list(verdict.missing)
    gaps.extend(
        f"Review incomplete due to missing fulfillment ({identity})." for identity in unmarked
    )
    return verdict.model_copy(
        update={"complete": False, "missing": list(dict.fromkeys(gaps)), "checks": checks}
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
    call_purpose: str | None = None,
    parallel_requirements: bool = False,
    expected_requirement_ids: set[str] | None = None,
    require_fulfillment: bool = False,
) -> ChatCompletionResult:
    """Validate provider-neutral JSON, allowing one format-only retry.

    Do not salvage partial objects or truncated output. A single enclosing Markdown
    fence is presentation only; schema and later exact-quote validation still apply.
    """
    if parallel_requirements and schema is CoverageVerdict and len(messages) == 2:
        payload = json.loads(messages[1].content)
        requirements = payload.get("requirements") or []
        if len(requirements) >= 6:
            # Independent overview duties share all source/authority context but
            # partition output work. Calculations and applicability are excluded
            # by the caller; they require a single interacting-rule verdict.
            midpoint = (len(requirements) + 1) // 2
            partitions = [requirements[:midpoint], requirements[midpoint:]]
            partition_messages = [
                [
                    replace(
                        messages[0],
                        content=messages[0].content
                        + "\nReview only this partition's requirement IDs. Other requested "
                        "duties are reviewed separately against the SAME evidence. Do not add "
                        "checks or gaps for other duties. Keep descriptions and answerable_scope "
                        "to 12 words each, and at most one concise missing item per requirement. "
                        "Mark complete only for this partition; the caller checks the union.",
                    ),
                    replace(
                        messages[1],
                        content=json.dumps(
                            {**payload, "requirements": partition},
                            ensure_ascii=False,
                        ),
                    ),
                ]
                for partition in partitions
            ]
            tasks = [
                asyncio.create_task(
                    _validated_completion(
                        llm,
                        partition,
                        schema=schema,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        proof_context=proof_context,
                        source_ids=source_ids,
                        truncation_retry_tokens=truncation_retry_tokens,
                        call_purpose=call_purpose,
                        expected_requirement_ids={item["requirement_id"] for item in partition_ids},
                        require_fulfillment=require_fulfillment,
                    )
                )
                for partition, partition_ids in zip(partition_messages, partitions, strict=True)
            ]
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            completions = [item for item in responses if isinstance(item, ChatCompletionResult)]
            if not completions:
                raise next(item for item in responses if isinstance(item, BaseException))
            verdicts = [
                CoverageVerdict.model_validate_json(item.content)
                if isinstance(item, ChatCompletionResult)
                else CoverageVerdict(
                    complete=False,
                    missing=["Review incomplete due to review protocol failure."],
                    checks=[],
                )
                for item in responses
            ]
            for index, (partition, verdict) in enumerate(zip(partitions, verdicts, strict=True)):
                expected = {item["requirement_id"] for item in partition}
                actual = [check.requirement_id for check in verdict.checks]
                if set(actual) != expected or len(actual) != len(expected):
                    # A malformed identity cannot prove its duty, but must not
                    # discard independently reviewed duties in the other partition.
                    # Keep only unique exact IDs; never guess an ID from description.
                    checks = []
                    gaps = list(verdict.missing)
                    for requirement in partition:
                        identity = requirement["requirement_id"]
                        matching = [c for c in verdict.checks if c.requirement_id == identity]
                        if len(matching) == 1:
                            checks.append(matching[0])
                        else:
                            description = requirement.get("description") or identity
                            checks.append(
                                _Check(
                                    requirement_id=identity,
                                    description=description,
                                    supported=False,
                                    evidence=[],
                                )
                            )
                            gaps.append(
                                f"{description}: review incomplete due to identity protocol "
                                f"error ({identity})."
                            )
                    if not gaps:
                        gaps.append(
                            "Review incomplete due to unassigned identity protocol error "
                            f"({', '.join(sorted(expected))})."
                        )
                    verdicts[index] = CoverageVerdict(
                        complete=False,
                        missing=list(dict.fromkeys(gaps))[:12],
                        checks=checks,
                    )
            missing = list(dict.fromkeys(item for verdict in verdicts for item in verdict.missing))
            merged = CoverageVerdict(
                complete=all(verdict.complete for verdict in verdicts),
                missing=missing,
                checks=[check for verdict in verdicts for check in verdict.checks],
            )
            # Each scope is independently reviewed, not inferred from support.
            merged.retain_answerable_scopes()
            usage = ChatUsage(0, 0)
            for item in completions:
                usage = _add_usage(usage, item.usage)
            return replace(completions[0], content=merged.model_dump_json(), usage=usage)
    work = _request_work(llm)
    if work is not None and call_purpose in _PURPOSE_COUNTERS:
        work.counts[_PURPOSE_COUNTERS[call_purpose]] += 1
    purpose = call_purpose
    usage = ChatUsage(0, 0)
    for attempt in range(2):
        cm = work.stage(purpose) if work is not None and purpose else nullcontext()
        with cm:
            completion = await generate_structured(
                llm,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                output_contract=StructuredOutput(schema.__name__, schema.model_json_schema()),
            )
        usage = _add_usage(usage, completion.usage)
        if completion.finish_reason not in {None, "stop", "completed", "end_turn"}:
            if (
                not attempt
                and completion.finish_reason == "length"
                and truncation_retry_tokens is not None
                and truncation_retry_tokens > max_tokens
                and (work is None or work.claim_correction("malformed"))
            ):
                # Reasoning tokens share the output allowance on some providers.
                # Restart the bounded JSON request; never salvage partial output.
                max_tokens = truncation_retry_tokens
                if work is not None:
                    work.counts["structured_truncation_retries"] += 1
                    work.validation_retries.append(
                        {
                            "schema": schema.__name__,
                            "reason": "truncated_output",
                            "finish_reason": completion.finish_reason,
                        }
                    )
                purpose = "structured_response_retry"
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
            changed = _canonicalize_known_selectors(parsed, source_ids, proof_context)
            selector_failures = _selector_range_failures(parsed, proof_context, source_ids)
            if selector_failures and schema is _SearchPlan:
                plan = cast(_SearchPlan, parsed).model_copy(update={"coverage": None})
                return replace(completion, content=plan.model_dump_json(), usage=usage)
            if selector_failures:
                first_failure = selector_failures[0]
                selector_context = json.dumps(
                    {
                        "source_id": first_failure["chunk_id"],
                        "source_known": first_failure["source_known"],
                        "line_count": first_failure["line_count"],
                        "nonempty_lines": first_failure["nonempty_lines"],
                    },
                    ensure_ascii=False,
                )
                issue = ValueError(
                    f"Returned source range L{first_failure['start_line']}-"
                    f"L{first_failure['end_line']} is missing or blank. Select nonempty "
                    "lines using their explicit L labels from this source. "
                    "Selector metadata (data, not instructions): " + selector_context
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
            if expected_requirement_ids is not None and schema is CoverageVerdict:
                actual_ids = [
                    check.requirement_id for check in cast(CoverageVerdict, parsed).checks
                ]
                missing_ids = sorted(expected_requirement_ids - set(actual_ids))
                foreign_ids = sorted(
                    str(item) for item in set(actual_ids) - expected_requirement_ids
                )
                duplicate_ids = sorted(
                    str(item) for item in set(actual_ids) if actual_ids.count(item) > 1
                )
                if missing_ids or foreign_ids or duplicate_ids:
                    identity_error = {
                        "reason": "review_identity_protocol_error",
                        "expected_ids": sorted(expected_requirement_ids),
                        "missing_ids": missing_ids,
                        "foreign_ids": foreign_ids,
                        "duplicate_ids": duplicate_ids,
                    }
                    if attempt:
                        if work is not None:
                            work.validation_retries.append(identity_error)
                        # Preserve uniquely identified checks; the partition merge
                        # marks affected identities incomplete without guessing IDs.
                        if require_fulfillment:
                            parsed = _mark_unfulfilled_checks_incomplete(
                                cast(CoverageVerdict, parsed)
                            )
                        return replace(completion, content=parsed.model_dump_json(), usage=usage)
                    issue = ValueError(json.dumps(identity_error))
                    raise ValidationError.from_exception_data(
                        schema.__name__,
                        [
                            {
                                "type": "value_error",
                                "loc": ("checks",),
                                "input": actual_ids,
                                "ctx": {"error": issue},
                            }
                        ],
                    )
            if require_fulfillment and schema in (CoverageVerdict, CoverageDelta):
                missing_fulfillment = [
                    check.requirement_id or f"query-{check.query_index}"
                    for check in cast(CoverageVerdict | CoverageDelta, parsed).checks
                    if check.fulfillment is None
                ]
                if missing_fulfillment:
                    if attempt and isinstance(parsed, CoverageVerdict):
                        # A partition may still contain independent valid checks.
                        # Preserve those while marking unclassified duties incomplete.
                        parsed = _mark_unfulfilled_checks_incomplete(parsed)
                        return replace(completion, content=parsed.model_dump_json(), usage=usage)
                    issue = ValueError(
                        "Each fresh review check needs explicit fulfillment: full, partial, "
                        "or none. Missing IDs: " + json.dumps(missing_fulfillment)
                    )
                    raise ValidationError.from_exception_data(
                        schema.__name__,
                        [
                            {
                                "type": "value_error",
                                "loc": ("checks",),
                                "input": missing_fulfillment,
                                "ctx": {"error": issue},
                            }
                        ],
                    )
            if changed:
                content = parsed.model_dump_json()
            if attempt:
                correction_schedule = _RECOVERY_SCHEDULE.get()
                if correction_schedule is not None:
                    correction_schedule.complete("selector_correction")
            return replace(completion, content=content, usage=usage)
        except ValidationError as exc:
            active_schedule = _RECOVERY_SCHEDULE.get()
            if active_schedule is not None and not active_schedule.admit(
                "selector_correction",
                requirement_ids=list(expected_requirement_ids or []),
                fingerprint=schema.__name__,
                expected_change="repair malformed selectors",
            ):
                raise
            if attempt or (work is not None and not work.claim_correction("malformed")):
                raise
            if work is not None:
                work.counts["structured_response_retries"] += 1
                work.validation_retries.append(
                    {
                        "schema": schema.__name__,
                        "reason": "schema_validation",
                        "issues": [
                            {
                                "type": str(error.get("type") or "validation_error"),
                                "path": ".".join(str(part) for part in error.get("loc") or ()),
                            }
                            for error in exc.errors(include_input=False)
                        ],
                    }
                )
            parsed_for_repair = None
            repair_failures: list[dict[str, Any]] = []
            try:
                parsed_for_repair = schema.model_validate_json(content)
                _canonicalize_known_selectors(parsed_for_repair, source_ids, proof_context)
                repair_failures = _selector_range_failures(
                    parsed_for_repair, proof_context, source_ids
                )
            except ValidationError:
                parsed_for_repair = None
            repair_messages: list[ChatMessage] | None = None
            isolated_selectors = (
                proof_context is not None
                and parsed_for_repair is not None
                and bool(repair_failures)
                and not _is_contradictory_completion(exc)
                and not _review_protocol_issues(
                    parsed_for_repair, expected_requirement_ids, require_fulfillment
                )
            )
            if isolated_selectors and parsed_for_repair is not None:
                repair_messages = _selector_repair_messages(
                    parsed_for_repair,
                    repair_failures,
                    proof_context or [],
                    source_ids,
                )
                isolated_selectors = repair_messages is not None
                if work is not None and work.validation_retries:
                    work.validation_retries[-1]["selector_failures"] = (
                        _selector_failure_diagnostics(repair_failures)
                    )
            if isolated_selectors and parsed_for_repair is not None and repair_messages is not None:
                purpose = "selector_retry"
                if work is not None:
                    work.counts["selector_retries"] += 1
                cm = work.stage(purpose) if work is not None else nullcontext()
                with cm:
                    repair_completion = await generate_structured(
                        llm,
                        repair_messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        output_contract=StructuredOutput(
                            "selector_repair", _SelectorRepairResponse.model_json_schema()
                        ),
                    )
                usage = _add_usage(usage, repair_completion.usage)
                if repair_completion.finish_reason not in {
                    None,
                    "stop",
                    "completed",
                    "end_turn",
                }:
                    return replace(repair_completion, usage=usage)
                repair_content = repair_completion.content.strip()
                repair_lines = repair_content.splitlines()
                if (
                    len(repair_lines) >= 3
                    and repair_lines[0].strip() in {"```json", "```"}
                    and repair_lines[-1].strip() == "```"
                ):
                    repair_content = "\n".join(repair_lines[1:-1])
                try:
                    repair = _SelectorRepairResponse.model_validate_json(repair_content)
                except ValidationError as repair_exc:
                    if work is not None:
                        work.counts["selector_retry_failures"] += 1
                        work.validation_retries.append(
                            {
                                "schema": _SelectorRepairResponse.__name__,
                                "reason": "selector_retry_invalid_response",
                                "issues": [
                                    {
                                        "type": str(error.get("type") or "validation_error"),
                                        "path": ".".join(
                                            str(part) for part in error.get("loc") or ()
                                        ),
                                    }
                                    for error in repair_exc.errors(include_input=False)
                                ],
                            }
                        )
                    raise exc from None
                if not _apply_selector_replacements(parsed_for_repair, repair, repair_failures):
                    if work is not None:
                        work.counts["selector_retry_failures"] += 1
                        work.validation_retries.append(
                            {
                                "schema": _SelectorRepairResponse.__name__,
                                "reason": "selector_retry_unmatched_replacement",
                            }
                        )
                    raise exc
                _canonicalize_known_selectors(parsed_for_repair, source_ids, proof_context)
                remaining = _selector_range_failures(parsed_for_repair, proof_context, source_ids)
                if remaining:
                    if work is not None:
                        work.counts["selector_retry_failures"] += 1
                        work.validation_retries.append(
                            {
                                "schema": _SelectorRepairResponse.__name__,
                                "reason": "selector_retry_invalid_replacement",
                                "selector_failures": _selector_failure_diagnostics(remaining),
                            }
                        )
                    raise exc
                protocol_issues = _review_protocol_issues(
                    parsed_for_repair, expected_requirement_ids, require_fulfillment
                )
                if protocol_issues:
                    if work is not None:
                        work.counts["selector_retry_failures"] += 1
                        work.validation_retries.append(
                            {
                                "schema": schema.__name__,
                                "reason": "selector_retry_protocol_error",
                                "issues": protocol_issues,
                            }
                        )
                    raise exc
                if active_schedule is not None:
                    active_schedule.complete("selector_correction")
                return replace(
                    repair_completion,
                    content=parsed_for_repair.model_dump_json(),
                    usage=usage,
                )
            # This is a complete-schema retry. Only the dedicated replacement
            # branch above is a selector retry; keep telemetry unambiguous.
            protocol_issues = (
                _review_protocol_issues(
                    parsed_for_repair, expected_requirement_ids, require_fulfillment
                )
                if parsed_for_repair is not None
                else []
            )
            selector_issues = _selector_failure_diagnostics(repair_failures)
            purpose = "structured_response_retry"
            messages = [
                *_structured_selector_retry(messages, proof_context, source_ids),
                ChatMessage(
                    role=ChatRole.SYSTEM,
                    content="Return a complete JSON object only, matching this schema. Do not add "
                    "Markdown or commentary. Re-evaluate the original supplied evidence; "
                    "do not invent missing facts. Schema: "
                    + json.dumps(schema.model_json_schema())
                    + " Validation issues: "
                    + json.dumps([error["msg"] for error in exc.errors(include_input=False)])
                    + " Selector issues: "
                    + json.dumps(selector_issues)
                    + " Protocol issues: "
                    + json.dumps(protocol_issues),
                ),
            ]
    raise AssertionError("bounded validation loop exhausted")


def _structured_selector_retry(
    messages: list[ChatMessage],
    context: list[ContextChunk] | None,
    source_ids: dict[str, str] | None,
) -> list[ChatMessage]:
    """Replace ambiguous labeled strings with copyable selectors on failed review only.

    Preserve the evidence, identifiers and question; do not append a duplicate
    context or change successful first-pass authoritative/tax review prompts.
    """
    if not context:
        return messages
    chunks = {str(chunk.chunk_id): chunk for chunk in context}
    retried = []
    for message in messages:
        if message.role != ChatRole.USER:
            retried.append(message)
            continue
        try:
            payload = json.loads(message.content)
        except (ValueError, TypeError):
            retried.append(message)
            continue
        records = payload.get("context") if isinstance(payload, dict) else None
        changed = False
        for record in records if isinstance(records, list) else []:
            if not isinstance(record, dict):
                continue
            label = record.get("chunk_id")
            if not isinstance(label, str):
                continue
            chunk = chunks.get((source_ids or {}).get(label, label))
            if chunk is None or record.get("content") != numbered_source_lines(chunk.content):
                continue
            del record["content"]
            record["source_lines"] = _source_line_records(chunk.content)
            changed = True
        retried.append(
            replace(message, content=json.dumps(payload, ensure_ascii=False))
            if changed
            else message
        )
    return retried


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
    required_coverage: dict[str, Any] | None = None,
    evidence_approach: str = "authoritative",
    timeout_seconds: float = REPAIR_TIMEOUT_SECONDS,
    max_initial_queries: int = MAX_REPAIR_DEPENDENCIES,
    max_followup_queries: int = 2,
    max_followup_rounds: int = MAX_REPAIR_FOLLOWUPS,
    recovery_profile: str = "legacy",
    allow_admitted_timeout_fallback: bool = False,
    parallel_overview_review: bool = False,
) -> EvidenceRepairResult:
    """Never mix active snapshots, relax filters, or promote unknown authority.

    All nonempty discovery branches must retain an admitted unit after the final budget. Model
    queries only retrieve candidates; they never become answer evidence. A failed
    repair leaves the original authority failure available to the caller's normal
    refusal policy. Unvalidated web snippets cannot bypass it. Two focused follow-ups
    are allowed inside the same timeout; there is no unbounded agent loop.
    """
    compatibility_profile = recovery_profile == "legacy" and _request_work(llm) is None
    max_initial_queries = min(
        max_initial_queries, MAX_REPAIR_DEPENDENCIES if compatibility_profile else 3
    )
    max_followup_queries = min(max_followup_queries, MAX_REPAIR_FOLLOWUPS)
    max_followup_rounds = min(max_followup_rounds, MAX_REPAIR_FOLLOWUPS)
    recovery_profile = (
        "legacy"
        if compatibility_profile
        else ("bounded" if recovery_profile == "legacy" else recovery_profile)
    )
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
        "version": "v28-conditional-facets-and-recovery-progress"
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
    started = monotonic()
    work = _request_work(llm)
    if work is not None:
        timeout_seconds = min(timeout_seconds, max(0.0, work.recovery_deadline - monotonic()))
    schedule = RecoverySchedule(
        started + timeout_seconds, enforce_reserves=work is not None, clock=monotonic
    )
    if work is not None and work.execution_policy == "adaptive_v1":
        max_initial_queries = min(max_initial_queries, 2)
        schedule.action_limits["search"] = work.recovery_search_credits
        schedule.action_limits["delta_review"] = 4
    if compatibility_profile:
        schedule.action_limits = {
            "search": max_initial_queries + max_followup_queries * max_followup_rounds,
            "structure": 2,
            "delta_review": max(2, max_followup_rounds),
            "selector_correction": 1,
        }
    schedule_token = _RECOVERY_SCHEDULE.set(schedule)
    diagnostics["recovery_actions"] = schedule.actions
    diagnostics["timeout_seconds"] = timeout_seconds
    diagnostics["recovery_budget"] = {
        "profile": recovery_profile,
        "timeout_seconds": timeout_seconds,
        "max_initial_queries": max_initial_queries,
        "max_followup_queries": max_followup_queries,
        "max_followup_rounds": max_followup_rounds,
        "deadline_kind": "shared_monotonic",
    }
    diagnostics["phase"] = "planning"
    if required_coverage:
        diagnostics["required_coverage_revalidation"] = True
    partial_checkpoint: EvidenceRepairResult | None = None
    admitted_timeout_fallback: list[ContextChunk] = []
    if allow_admitted_timeout_fallback and initial_decision is not None:
        admitted_units = {(unit.chunk_id, unit.content) for unit in initial_decision.admitted_units}
        admitted_timeout_fallback = [
            chunk
            for chunk in selected
            if chunk.metadata.get("authority_status") != "unresolved"
            and (chunk.chunk_id, chunk.content) in admitted_units
        ]
    if timeout_seconds <= 0:
        diagnostics["stop_reason"] = "recovery_deadline_exceeded"
        diagnostics["failure_reason"] = "deadline_exceeded"
        diagnostics["requirement_progress"] = {"stop_reason": "recovery_deadline_exceeded"}
        if (
            initial_decision is not None
            and initial_decision.sufficient
            and admitted_timeout_fallback
        ):
            _restore_admitted_timeout_fallback(
                result, diagnostics, initial_decision, admitted_timeout_fallback
            )
        else:
            diagnostics["status"] = "repair_unavailable"
            result.failure = ProviderTimeoutError(
                "Evidence review exceeded its time limit.",
                provider_name=llm.provider_name,
                context={"reason": "recovery_deadline_exceeded"},
            )
        return result
    reference_date = (
        inputs.as_of.date().isoformat()
        if inputs.as_of
        else initial.diagnostics.get("reference_date")
    )
    authority_date: date | None = None
    if inputs.as_of is not None:
        authority_date = inputs.as_of.date()
    elif reference_date:
        try:
            authority_date = date.fromisoformat(str(reference_date)[:10])
        except ValueError:
            authority_date = None
    trusted_context = (
        "Normalized request scope: " + inputs.normalized_scope.model_dump_json() + "\n"
    )
    if reference_date:
        trusted_context += f"Trusted retrieval reference date: {reference_date}\n"
    language_inventory = initial.diagnostics.get("corpus_language_inventory")
    if isinstance(language_inventory, dict):
        supported_inventory = {
            language: count
            for language, count in language_inventory.items()
            if language in DEFAULT_SUPPORTED_TARGET_LANGUAGES
            and isinstance(count, int)
            and count > 0
        }
        if supported_inventory:
            trusted_context += (
                "Active index chunk languages (discovery hints, not rule proof): "
                + json.dumps(supported_inventory, sort_keys=True)
                + ". Cover the principal rule in its source language when the user asks "
                "in a different language; a translated title is not the source text.\n"
            )
            question_language = detect_language(inputs.query).primary_language
            dominant_language = max(supported_inventory, key=supported_inventory.__getitem__)
            if (
                question_language in DEFAULT_SUPPORTED_TARGET_LANGUAGES
                and dominant_language != question_language
                and supported_inventory[dominant_language]
                > supported_inventory.get(question_language, 0)
            ):
                trusted_context += (
                    "Required discovery route: include at least one INITIAL search query "
                    f"for the principal governing rule in {dominant_language} source-language "
                    "script. Bind it to the governing or central requirement ID and keep it "
                    "within the existing query allowance. Translate the legal concept, not "
                    "merely the document title. This route is discovery, not proof.\n"
                )
    if domain_instructions.strip():
        trusted_context += f"Trusted Project domain instructions:\n{domain_instructions.strip()}\n"
    if not authoritative_compatibility:
        trusted_context += f"Evidence approach: {evidence_approach}\n"
    trusted_context += (
        "Bounded recovery allowance: produce at most "
        f"{max_initial_queries} initial search queries and at most "
        f"{max_followup_queries} continuation queries across "
        f"{max_followup_rounds} follow-up rounds. "
        "Assign requirement materiality and order governing applicability and central rules "
        "before adjacent rules and secondary details. Preserve every required item even when "
        "its query will fall outside the allowance.\n"
    )
    trusted_context += (
        "For a numeric rate or threshold, search the governing category and period "
        "heading together with the operative table or rule; try source-language "
        "terms when the corpus uses another language. A proposed table does not "
        "establish the operative value. Keep these searches inside the stated allowance.\n"
    )
    snapshot = tuple(
        initial.diagnostics.get(k) for k in ("index_build_id", "source_metadata_generation")
    )
    if any(value is None for value in snapshot):
        diagnostics["status"] = "snapshot_unavailable"
        return result
    review_seconds = (
        max(0.0, work.phase_deadline("coverage_review") - monotonic())
        if work is not None and work.execution_policy == "adaptive_v1"
        else timeout_seconds
    )
    timeout_context = asyncio.timeout(review_seconds)
    try:
        async with timeout_context:
            # An attempted call with missing usage (including a timeout) is
            # unknown cost, not a free operation in the combined turn usage.
            result.usage = ChatUsage(None, None)
            hints = _source_hints(
                [*selected, *initial.chunks],
                include_work_metadata=not authoritative_compatibility,
            )
            safe_selected = [
                chunk
                for chunk in selected
                if chunk.metadata.get("authority_status") != "unresolved"
            ]
            if work is not None and work.execution_policy == "adaptive_v1" and not safe_selected:
                safe_selected = ContextBuilder(
                    chat_config, evidence_approach=evidence_approach
                ).select(
                    remove_superseded_provisions(
                        initial.chunks,
                        list(initial.diagnostics.get("modifies_expansion_records") or []),
                        reference_date=authority_date,
                    )
                )
                safe_selected = [
                    chunk
                    for chunk in safe_selected
                    if chunk.metadata.get("authority_status") != "unresolved"
                ]
            include_plan_proof = bool(safe_selected) and initial_decision is not None
            diagnostics["initial_coverage_review"] = (
                "admitted_evidence" if include_plan_proof else "requires_recovery"
            )
            initial_source_ids = {f"E{i}": str(c.chunk_id) for i, c in enumerate(safe_selected, 1)}
            initial_labels = {identifier: label for label, identifier in initial_source_ids.items()}
            selected = list(safe_selected)
            diagnostics["retained_initial_chunk_ids"] = [str(c.chunk_id) for c in selected]
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
                                **(
                                    {"prior_validated_scope_constraints": required_coverage}
                                    if required_coverage
                                    else {}
                                ),
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
                                        ]
                                    }
                                    if include_plan_proof
                                    else {}
                                ),
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                    ),
                ],
                temperature=None,
                max_tokens=min(4096, max_output_tokens),
                truncation_retry_tokens=min(8192, max_output_tokens)
                if authoritative_compatibility
                else None,
                proof_context=selected if include_plan_proof else None,
                source_ids=initial_source_ids,
                schema=_SearchPlan,
                call_purpose="recovery_planning",
            )
            result.usage = completion.usage or ChatUsage(None, None)
            diagnostics["planning_finish_reason"] = completion.finish_reason
            if completion.finish_reason not in {None, "stop", "completed", "end_turn"}:
                diagnostics["status"] = "incomplete_plan"
                return result
            plan = _SearchPlan.model_validate_json(completion.content)
            plan.requirements = [
                r.model_copy(
                    update={"assigned_scope": inputs.normalized_scope.model_dump(mode="json")}
                )
                for r in plan.requirements
            ]
            all_requirement_ids = {r.requirement_id for r in plan.requirements}
            if len(all_requirement_ids) != len(plan.requirements):
                diagnostics["status"] = "invalid_plan"
                return result
            requirements, planned_queries, _planned_ownership = _prepare_search_plan(
                plan, inputs.query
            )
            requirement_ids = {r.requirement_id for r in requirements}
            mandatory_partial_ids = _mandatory_partial_ids(inputs.query, requirements)
            diagnostics["requirements"] = [r.model_dump() for r in requirements]
            ignored_optional = [
                r.model_dump() for r in plan.requirements if r.requirement_id not in requirement_ids
            ]
            if ignored_optional:
                diagnostics["optional_requirements_ignored"] = ignored_optional
            proof_map = _TurnProofMap(requirements)
            proof_map.remember_records(
                list(initial.diagnostics.get("modifies_expansion_records") or [])
            )
            if plan.coverage is not None:
                proof_map.remember_gaps(list(plan.coverage.missing), plan.coverage.checks)
                proof_map.remember_partial(plan.coverage.partial_answer)
                for check in plan.coverage.checks:
                    for quote in check.evidence:
                        quote.chunk_id = initial_source_ids.get(quote.chunk_id, quote.chunk_id)
            selected = [c for c in selected if c.metadata.get("authority_status") != "unresolved"]
            initial_records = list(initial.diagnostics.get("modifies_expansion_records") or [])
            initial_ranges_valid = bool(
                plan.coverage is not None and plan.coverage.resolve_source_ranges(selected)
            )
            if initial_ranges_valid and plan.coverage is not None:
                plan.coverage = cast(
                    CoverageVerdict,
                    _guard_review_fulfillment(plan.coverage, requirements, selected, inputs.query),
                )
            if (
                requirement_ids
                and plan.coverage is not None
                and initial_decision is not None
                and initial_ranges_valid
                and plan.coverage.validates([], selected, requirement_ids)
                and all(check.fulfillment == "full" for check in plan.coverage.checks)
            ):
                proof_ids = {q.chunk_id for c in plan.coverage.checks for q in c.evidence}
                result.selected = [c for c in selected if str(c.chunk_id) in proof_ids]
                result.selected = annotate_authority_limitations(
                    result.selected,
                    initial_records,
                    reference_date=authority_date,
                )
                if any(c.metadata.get("authority_status") == "unresolved" for c in result.selected):
                    diagnostics["status"] = "dependency_unresolved"
                    result.selected = []
                    return result
                proof_map.accept_confirmed(plan.coverage.checks, result.selected, initial_records)
                diagnostics["proof_map"] = proof_map.diagnostics()
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
                result.answerable_scope = _answerable_scope(
                    complete=True,
                    partial=None,
                    missing=(),
                    missing_inputs=result.missing_inputs,
                    supported_requirement_ids=sorted(proof_map.proven_ids()),
                )
                result.answerable_scope["reviewed_scopes"] = _reviewed_scopes(plan.coverage, None)
                result.selected = [
                    replace(
                        chunk,
                        metadata={
                            **chunk.metadata,
                            "reviewed_proof": [
                                {
                                    "requirement_id": check.requirement_id,
                                    "quote": quote.quote,
                                    "fulfillment": check.fulfillment,
                                    "supported_scope": check.answerable_scope or check.description,
                                }
                                for check in plan.coverage.checks
                                for quote in check.evidence
                                if quote.chunk_id == str(chunk.chunk_id)
                            ],
                        },
                    )
                    for chunk in result.selected
                ]
                diagnostics["answerable_scope"] = result.answerable_scope
                return result
            if requirement_ids and plan.coverage is not None:
                if initial_ranges_valid:
                    sources = {str(c.chunk_id): _quote_tokens(c.content) for c in selected}
                    confirmed_checks = [
                        check
                        for check in plan.coverage.checks
                        if check.fulfillment in ("full", "partial")
                        and _check_confirmed(check, sources)
                    ]
                    proof_map.accept_confirmed(confirmed_checks, selected, initial_records)
                    confirmed = {
                        item.chunk_id for check in confirmed_checks for item in check.evidence
                    }
                    selected = [c for c in selected if str(c.chunk_id) in confirmed]
                    diagnostics["initial_coverage_status"] = "partial"
                    initial_verdict = proof_map.canonical_verdict(
                        missing=list(plan.coverage.missing),
                        gap_kinds=list(plan.coverage.gap_kinds),
                        partial_answer=plan.coverage.partial_answer,
                    )
                    initial_verdict.retain_answerable_scopes()
                    _limit_numeric_partial_scope(
                        initial_verdict, mandatory_partial_ids, inputs.query
                    )
                    initial_partial_valid = initial_verdict.partial_validates(
                        selected, requirement_ids, mandatory_partial_ids
                    )
                    if initial_partial_valid:
                        diagnostics["coverage"] = {
                            **_coverage_diagnostics(initial_verdict, False),
                            "gap_kinds": initial_verdict.gap_kinds
                            or ["source_rule"] * len(initial_verdict.missing),
                            "source_ranges_validated": True,
                            "partial_scope_validated": True,
                        }
                        _store_partial_answer(diagnostics, initial_verdict)
                        diagnostics["requirement_progress"] = _requirement_progress(
                            initial_verdict,
                            focused_ids=[],
                            adjacent_queries=[],
                            attempts=[],
                            stop_reason=None,
                        )
                        checkpoint = EvidenceRepairResult([], None, deepcopy(diagnostics))
                        _handoff_reviewed_proof(
                            checkpoint,
                            initial_verdict,
                            selected,
                            [],
                            requirement_ids,
                            [initial_decision] if initial_decision is not None else [],
                            mandatory_partial_ids,
                        )
                        if checkpoint.decision is not None:
                            partial_checkpoint = checkpoint
                            diagnostics["initial_partial_checkpoint"] = "validated"
                else:
                    diagnostics["initial_coverage_status"] = "invalid"
            requirements, queries, query_requirement_ids = _prepare_search_plan(
                plan,
                inputs.query,
                proven_ids=proof_map.proven_ids(),
                balanced_overview=recovery_profile == "broad",
            )
            diagnostics["discovery_query_normalization"] = [
                {
                    "original_query": entry.query,
                    "executable_query": executable,
                    "reason": reason,
                    "requirement_ids": entry.requirement_ids,
                }
                for entry in plan.queries
                for executable, reason in [
                    _normalize_discovery_query(entry.query.strip(), inputs.query)
                ]
            ]
            if work is not None and work.execution_policy == "adaptive_v1":
                gap_binding = initial.diagnostics.get("known_corpus_gap_binding") or {}
                known_gap = (
                    gap_binding.get("producer") == "source_metadata_dependency.v1"
                    and gap_binding.get("index_build_id")
                    == str(initial.diagnostics.get("index_build_id"))
                    and gap_binding.get("source_metadata_generation")
                    == initial.diagnostics.get("source_metadata_generation")
                    and gap_binding.get("project_id") == str(work.project_id)
                )
                recoverable = bool(selected and queries and requirement_ids and not known_gap)
                if recoverable and (
                    len(requirement_ids - proof_map.proven_ids()) > 1
                    or any(r.depends_on for r in requirements)
                ):
                    work.promote(
                        next(
                            iter(sorted(requirement_ids - proof_map.proven_ids())),
                            "required_dependency",
                        ),
                        recoverable=True,
                        required_seconds=6.0,
                    )
                    schedule.deadline = work.recovery_deadline
                    timeout_context.reschedule(
                        asyncio.get_running_loop().time()
                        + max(0.0, work.phase_deadline("coverage_review") - monotonic())
                    )
                    schedule.action_limits["search"] = work.recovery_search_credits
                if known_gap:
                    diagnostics["status"] = "coverage_incomplete"
                    diagnostics["stop_reason"] = "known_corpus_gap"
                    diagnostics["queries"] = []
                    diagnostics["coverage"] = {
                        "missing": list(
                            initial.diagnostics.get("known_corpus_gap_requirements")
                            or [r.description for r in requirements]
                        ),
                        "complete": False,
                        "quotes_validated": False,
                    }
                    if partial_checkpoint is not None:
                        partial_checkpoint.diagnostics["stop_reason"] = "known_corpus_gap"
                        return partial_checkpoint
                    return result
            queries = queries[:max_initial_queries]
            query_requirement_ids = {
                query: query_requirement_ids.get(query, []) for query in queries
            }
            diagnostics["proof_map"] = proof_map.diagnostics()
            planned_invalid = any(not query or len(query) > 500 for query in planned_queries)
            remaining_invalid = any(not query or len(query) > 500 for query in queries)
            if planned_invalid or remaining_invalid:
                diagnostics["status"] = "invalid_plan"
                return result
            if not queries:
                if planned_queries and proof_map.proven_ids():
                    verdict = proof_map.snapshot_verdict()
                    diagnostics["coverage"] = {
                        **_coverage_diagnostics(verdict, verdict.complete),
                        "gap_kinds": verdict.gap_kinds or ["source_rule"] * len(verdict.missing),
                    }
                    if verdict.partial_answer is not None:
                        _store_partial_answer(diagnostics, verdict)
                    _handoff_reviewed_proof(
                        result,
                        verdict,
                        selected,
                        [],
                        requirement_ids,
                        [initial_decision] if initial_decision else [],
                        mandatory_partial_ids,
                    )
                    diagnostics["queries"] = []
                    return result
                if partial_checkpoint is not None:
                    _restore_partial_checkpoint(
                        result,
                        diagnostics,
                        partial_checkpoint,
                        stop_reason="no_discovery_queries",
                    )
                    diagnostics["queries"] = []
                    return result
                diagnostics["status"] = "invalid_plan"
                return result
            diagnostics["queries"] = queries
            diagnostics["branches"] = []
            diagnostics["focused_requirement_ids"] = []
            diagnostics["requirement_attempts"] = []
            groups: list[list[ContextChunk]] = []
            raw_groups: list[list[ContextChunk]] = []
            decisions: list[EvidenceDecision] = [initial_decision] if initial_decision else []
            records = list(initial.diagnostics.get("modifies_expansion_records") or [])
            pending_queries = list(queries)
            pending_routes = dict.fromkeys(pending_queries, "search")
            adjacent_requests: dict[str, list[uuid.UUID]] = {}
            attempted_adjacent: set[tuple[str, uuid.UUID]] = set()
            remaining_followup_queries = (
                max(0, work.recovery_search_credits - len(queries))
                if work is not None and work.execution_policy == "adaptive_v1"
                else min(max_followup_queries, max(0, 3 - len(queries)))
            )
            reviewed_evidence: set[str] = set()
            last_verdict: CoverageVerdict | None = None
            last_partial_scope_validated = False
            # Structural completion has its own one-round allowance. It cannot
            # spend semantic discovery slots or bypass the captured source policy.
            structural_rounds = 0
            structural_pending: list[ContextChunk] = []
            semantic_rounds = 0
            for round_index in range(3 + max_followup_rounds):
                if recovery_profile == "legacy" and round_index > 0:
                    # The disabled/legacy contract is two continuation queries
                    # per round. Shared-total accounting is opt-in with the
                    # bounded focused/broad profiles only.
                    remaining_followup_queries = max_followup_queries
                diagnostics["phase"] = "retrieval"
                admitted_queries = []
                for query in pending_queries:
                    route = "structure" if query in adjacent_requests else "search"
                    if schedule.admit(
                        route,
                        requirement_ids=list(query_requirement_ids.get(query, requirement_ids)),
                        fingerprint=content_hash(
                            json.dumps(
                                {
                                    "query": " ".join(query.casefold().split()),
                                    "scope": inputs.normalized_scope.model_dump(mode="json"),
                                    "requirements": sorted(
                                        query_requirement_ids.get(query, requirement_ids)
                                    ),
                                    "snapshot": snapshot,
                                    "anchors": sorted(
                                        str(value) for value in adjacent_requests.get(query, [])
                                    ),
                                    "evidence": sorted(_chunk_content_hash(c) for c in selected),
                                },
                                sort_keys=True,
                                default=str,
                            )
                        ),
                        expected_change="complete missing source structure"
                        if route == "structure"
                        else "find central unresolved rule",
                    ):
                        admitted_queries.append(query)
                pending_queries = admitted_queries
                if not pending_queries:
                    diagnostics["stop_reason"] = schedule.stop_reason or "no_new_evidence_route"
                    break
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
                    if schedule.search_deadline <= monotonic():
                        raise TimeoutError
                    branches = await batch_retrieve(
                        requests,
                        snapshot={
                            **initial.diagnostics,
                            "discovery_deadline": schedule.search_deadline,
                        },
                    )
                else:
                    # Compatibility ports can share a session and must remain sequential.
                    branches = []
                    for request in requests:
                        async with asyncio.timeout(
                            max(0.0, schedule.search_deadline - monotonic())
                        ):
                            if schedule.search_deadline <= monotonic():
                                raise TimeoutError
                            try:
                                branch = await retrieval.retrieve(**request)
                            except (ProviderTimeoutError, TimeoutError):
                                branch = ContextRetrievalResult(
                                    chunks=[],
                                    diagnostics={
                                        **initial.diagnostics,
                                        "branch_failure": "provider_or_deadline_failure",
                                    },
                                )
                        if (
                            tuple(
                                branch.diagnostics.get(k)
                                for k in ("index_build_id", "source_metadata_generation")
                            )
                            != snapshot
                        ):
                            diagnostics["status"] = "snapshot_changed"
                            return result
                        if partial_checkpoint is not None and not _checkpoint_matches_branch(
                            partial_checkpoint,
                            None,
                            branch,
                            records,
                            diagnostics.get("requirements", []),
                        ):
                            diagnostics["partial_checkpoint_invalidated"] = (
                                "proof_content_requirement_or_authority_changed"
                            )
                            partial_checkpoint = None
                        branches.append(branch)
                # Inspect every returned branch before admission can await a provider.
                for branch in branches:
                    branch_snapshot = tuple(
                        branch.diagnostics.get(k)
                        for k in ("index_build_id", "source_metadata_generation")
                    )
                    if branch_snapshot != snapshot:
                        diagnostics["status"] = "snapshot_changed"
                        return result
                    if partial_checkpoint is not None and not _checkpoint_matches_branch(
                        partial_checkpoint,
                        None,
                        branch,
                        records,
                        diagnostics.get("requirements", []),
                    ):
                        # New authority or changed proof text invalidates the old review.
                        diagnostics["partial_checkpoint_invalidated"] = (
                            "proof_content_requirement_or_authority_changed"
                        )
                        partial_checkpoint = None
                round_structural_anchors: list[ContextChunk] = []
                for query, branch in zip(pending_queries, branches, strict=True):
                    schedule.complete(
                        "structure" if query in adjacent_requests else "search",
                        changed=bool(branch.chunks),
                    )
                    records.extend(branch.diagnostics.get("modifies_expansion_records") or [])
                    raw_groups.append(branch.chunks)
                    scoped = remove_superseded_provisions(
                        branch.chunks, records, reference_date=authority_date
                    )
                    round_structural_anchors.extend(
                        chunk
                        for chunk in scoped
                        if chunk.metadata.get("table_context_status") == "context_exceeds_budget"
                        or chunk.metadata.get("heading_context_status") == "context_exceeds_budget"
                        or (
                            round_index > 0
                            and re.search(
                                r"continues?\b|following (?:paragraph|page|table)|"
                                r"continued|\.\.\.$|পরবর্তী|অব্যাহত",
                                chunk.content,
                                re.I,
                            )
                        )
                    )
                    safe = [
                        c
                        for c in scoped
                        if c.metadata.get("authority_status") != "unresolved"
                        and c.metadata.get("table_context_status") != "context_exceeds_budget"
                        and c.metadata.get("heading_context_status") != "context_exceeds_budget"
                    ]
                    diagnostics["phase"] = "admission"
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
                        reference_date=authority_date,
                    )
                    route = pending_routes.get(
                        query, "adjacent" if query in adjacent_requests else "search"
                    )
                    attempted_ids = list(query_requirement_ids.get(query, []))
                    diagnostics["branches"].append(
                        {
                            "query": query,
                            "discovery_route": route,
                            "requirement_ids": attempted_ids,
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
                    diagnostics["requirement_attempts"].append(
                        {
                            "route": route,
                            "query": query,
                            "requirement_ids": attempted_ids,
                            "status": "executed",
                            "candidate_ids": [str(c.chunk_id) for c in branch.chunks],
                            "admitted_ids": [str(c.chunk_id) for c in units],
                        }
                    )
                    if route == "focused":
                        diagnostics["focused_requirement_ids"] = list(
                            dict.fromkeys(
                                [
                                    *(diagnostics.get("focused_requirement_ids") or []),
                                    *attempted_ids,
                                ]
                            )
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
                if requirement_ids and len(queries) >= 4:
                    # Broad initial hits must not consume every slot before a
                    # focused dependency can contribute its strongest evidence.
                    # Keep the existing admitted span when an ID is rediscovered.
                    existing = {c.chunk_id: c for c in reversed(ordered)}
                    heads = [existing[g[0].chunk_id] for g in groups if g]
                    # Preserve discovery breadth beyond the first hit. Initial
                    # broad-query hits otherwise crowd out the second governing
                    # passage of each focused duty (e.g. duty versus deadline).
                    focused = [
                        existing[group[i].chunk_id]
                        for i in range(REPAIR_CHUNKS_PER_DEPENDENCY)
                        for group in groups
                        if len(group) > i
                    ]
                    ordered = [*heads, *focused, *ordered]
                # Two searches can admit different spans of the same chunk. Use
                # one intact admitted span; never merge them or compare the first
                # span with a later duplicate's content in the mutation guard.
                unique_ordered: dict[uuid.UUID, ContextChunk] = {}
                for candidate in ordered:
                    unique_ordered.setdefault(candidate.chunk_id, candidate)
                ordered = list(unique_ordered.values())
                reconciled = remove_superseded_provisions(
                    ordered, records, reference_date=authority_date
                )
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
                    reference_date=authority_date,
                )
                if (
                    round_structural_anchors
                    and structural_rounds < 2
                    and getattr(retrieval, "supports_adjacent_retrieval", False) is True
                ):
                    # Keep policy/snapshot-scoped anchors outside the admitted
                    # proof. Adjacent retrieval may supply a preserved replacement.
                    query = (pending_queries[0] if pending_queries else inputs.query)[:500]
                    all_anchors = list(
                        {chunk.chunk_id: chunk for chunk in round_structural_anchors}.values()
                    )[:4]
                    structural_pending = [
                        chunk
                        for chunk in all_anchors
                        if chunk.metadata.get("table_context_status") == "context_exceeds_budget"
                        or chunk.metadata.get("heading_context_status") == "context_exceeds_budget"
                    ]
                    anchors = [chunk.chunk_id for chunk in all_anchors]
                    adjacent_requests = {query: anchors}
                    pending_queries = [query]
                    pending_routes = {query: "adjacent"}
                    query_requirement_ids[query] = sorted(requirement_ids)
                    queries.append(query)
                    structural_rounds += 1
                    diagnostics["structural_completion_rounds"] = structural_rounds
                    diagnostics.setdefault("adjacent_queries", []).append(query)
                    selected = [
                        chunk
                        for chunk in budgeted
                        if chunk.metadata.get("authority_status") != "unresolved"
                    ]
                    continue
                if structural_pending and any(
                    not any(
                        replacement.document_id == anchor.document_id
                        and replacement.metadata.get(
                            "table_context_status"
                            if anchor.metadata.get("table_context_status")
                            == "context_exceeds_budget"
                            else "heading_context_status"
                        )
                        == "preserved"
                        and replacement.metadata.get("authority_status") != "unresolved"
                        for replacement in budgeted
                    )
                    for anchor in structural_pending
                ):
                    diagnostics["status"] = "dependency_unresolved"
                    diagnostics["structural_context_unresolved"] = [
                        str(anchor.chunk_id) for anchor in structural_pending
                    ]
                    return result
                if any(c.metadata.get("authority_status") == "unresolved" for c in budgeted):
                    diagnostics["status"] = "dependency_unresolved"
                    return result
                retained = {c.chunk_id for c in budgeted}
                if not requirement_ids and any(
                    group and not any(c.chunk_id in retained for c in group) for group in groups
                ):
                    diagnostics["status"] = "dependency_exceeds_budget"
                    return result
                if requirement_ids:
                    previously_valid = proof_map.proven_ids()
                    reviewing = proof_map.invalidate_changed(
                        budgeted,
                        records,
                        discovered=[
                            chunk for group in groups[-len(pending_queries) :] for chunk in group
                        ],
                    )
                    invalidated_proven = previously_valid & reviewing
                    proof_map.remember_records(records)
                    previous_proof_ids = {
                        evidence_id
                        for facet in proof_map._facets.values()
                        if facet.valid
                        for evidence_id in facet.evidence_ids
                    }
                    review_chunk_ids = {
                        str(chunk.chunk_id)
                        for chunk in budgeted
                        if str(chunk.chunk_id) not in previous_proof_ids
                    }
                    for req_id in reviewing:
                        review_chunk_ids.update(proof_map._facets[req_id].evidence_ids)
                    review_chunks = [
                        chunk for chunk in budgeted if str(chunk.chunk_id) in review_chunk_ids
                    ] or list(budgeted)
                    review_key = _review_evidence_key(
                        review_chunks,
                        records,
                        diagnostics["requirements"],
                        unresolved_ids=sorted(reviewing),
                    )
                    input_key = _review_evidence_key(
                        review_chunks, records, diagnostics["requirements"]
                    )
                    if not invalidated_proven and (
                        review_key in reviewed_evidence or input_key in reviewed_evidence
                    ):
                        # A new wording/rank does not supply new legal proof.
                        diagnostics["status"] = "coverage_incomplete"
                        diagnostics["stop_reason"] = "unchanged_review_evidence"
                        diagnostics["coverage_reviews_skipped"] = 1
                        if last_verdict is not None:
                            diagnostics["requirement_progress"] = _requirement_progress(
                                last_verdict,
                                focused_ids=diagnostics.get("focused_requirement_ids") or [],
                                adjacent_queries=diagnostics.get("adjacent_queries") or [],
                                attempts=diagnostics.get("requirement_attempts") or [],
                                stop_reason="unchanged_review_evidence",
                            )
                        if last_partial_scope_validated and last_verdict is not None:
                            verdict = last_verdict
                            _store_partial_answer(diagnostics, last_verdict)
                            break
                        return result
                    reviewed_evidence.add(review_key)
                    reviewed_evidence.add(input_key)
                    delta_review = round_index > 0
                else:
                    reviewing = set()
                    review_chunks = budgeted
                    delta_review = round_index > 0
                if release_read_transaction is not None:
                    await release_read_transaction()
                planning_usage = result.usage
                result.usage = ChatUsage(None, None)
                # Short passage labels prevent the model from confusing long UUIDs
                # belonging to different excerpts of the same document. The map is
                # local to this final context; persisted provenance keeps real IDs.
                review_context = review_chunks if delta_review else budgeted
                labels = {str(c.chunk_id): f"E{i}" for i, c in enumerate(review_context, start=1)}
                source_ids = {label: source_id for source_id, label in labels.items()}
                if delta_review and not schedule.admit(
                    "delta_review",
                    requirement_ids=sorted(reviewing or requirement_ids),
                    fingerprint=_review_evidence_key(
                        review_context, records, diagnostics.get("requirements", [])
                    ),
                    expected_change="review only changed or unresolved proof",
                ):
                    diagnostics["stop_reason"] = schedule.stop_reason
                    break
                diagnostics["phase"] = "coverage_review"
                review_requirements = [
                    item
                    for item in diagnostics["requirements"]
                    if not delta_review or item["requirement_id"] in reviewing
                ]
                diagnostics.setdefault("review_inputs", []).append(
                    {
                        "requirement_ids": [item["requirement_id"] for item in review_requirements],
                        "partition_requirement_ids": (
                            [
                                [
                                    item["requirement_id"]
                                    for item in review_requirements[
                                        : (len(review_requirements) + 1) // 2
                                    ]
                                ],
                                [
                                    item["requirement_id"]
                                    for item in review_requirements[
                                        (len(review_requirements) + 1) // 2 :
                                    ]
                                ],
                            ]
                            if parallel_overview_review
                            and not delta_review
                            and len(review_requirements) >= 6
                            else []
                        ),
                        "chunk_ids": [str(chunk.chunk_id) for chunk in review_context],
                        "evidence_unit_ids": [
                            str(chunk.metadata.get("evidence_unit_id"))
                            for chunk in review_context
                            if chunk.metadata.get("evidence_unit_id")
                        ],
                        "branch_candidates": [
                            {
                                "query": attempt["query"],
                                "requirement_ids": attempt["requirement_ids"],
                                "selected_ids": attempt["admitted_ids"],
                                "omitted_candidate_ids": [
                                    candidate
                                    for candidate in attempt["candidate_ids"]
                                    if candidate not in attempt["admitted_ids"]
                                ],
                            }
                            for attempt in diagnostics.get("requirement_attempts") or []
                        ],
                    }
                )
                verification = await _validated_completion(
                    llm,
                    [
                        ChatMessage(
                            role=ChatRole.SYSTEM,
                            content=trusted_context
                            + coverage_prompt
                            + PARTIAL_COVERAGE_PROMPT
                            + (DELTA_COVERAGE_PROMPT if delta_review else ""),
                        ),
                        ChatMessage(
                            role=ChatRole.USER,
                            content=json.dumps(
                                {
                                    "original_question": inputs.query,
                                    **(
                                        {"requirements": review_requirements}
                                        if requirement_ids
                                        else {}
                                    ),
                                    **(
                                        {
                                            "review_mode": "changed_or_unresolved_facets",
                                            "canonical_requirement_ids": sorted(requirement_ids),
                                            "retained_supported": [
                                                {
                                                    "requirement_id": facet.requirement_id,
                                                    "description": facet.description,
                                                }
                                                for facet in proof_map._facets.values()
                                                if facet.valid
                                            ],
                                        }
                                        if delta_review
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
                                    "authority_limitations": [
                                        record
                                        for record in _unique_authority_records(records)
                                        if not delta_review
                                        or any(
                                            authority_record_affects_chunk(record, chunk)
                                            for chunk in review_context
                                        )
                                    ],
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
                                            review_context
                                            if authoritative_compatibility
                                            else sorted(
                                                review_context,
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
                    schema=CoverageDelta if delta_review else CoverageVerdict,
                    proof_context=review_context if requirement_ids else None,
                    source_ids=source_ids,
                    call_purpose="coverage_review",
                    parallel_requirements=parallel_overview_review and not delta_review,
                    require_fulfillment=True,
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
                if delta_review:
                    schedule.complete("delta_review", changed=True)
                if verification.finish_reason not in {None, "stop", "completed", "end_turn"}:
                    diagnostics["status"] = "coverage_incomplete"
                    if partial_checkpoint is not None:
                        _restore_partial_checkpoint(
                            result,
                            diagnostics,
                            partial_checkpoint,
                            stop_reason="coverage_review_incomplete",
                        )
                    return result
                if delta_review:
                    parsed_review: CoverageDelta | CoverageVerdict = (
                        CoverageDelta.model_validate_json(verification.content)
                    )
                else:
                    parsed_review = CoverageVerdict.model_validate_json(verification.content)
                if requirement_ids:
                    unknown_checks = [
                        check
                        for check in parsed_review.checks
                        if check.requirement_id and check.requirement_id not in requirement_ids
                    ]
                    if unknown_checks:
                        # The coverage reviewer verifies the bounded plan. It cannot
                        # silently enlarge the question and spend another recovery round.
                        diagnostics.setdefault("reviewer_requirements_ignored", []).extend(
                            {
                                "requirement_id": check.requirement_id,
                                "description": check.description,
                            }
                            for check in unknown_checks
                        )
                        unknown_descriptions = {
                            " ".join(check.description.split()).casefold()
                            for check in unknown_checks
                            if check.description.strip()
                        }
                        kept_missing: list[str] = []
                        kept_gap_kinds: list[str] = []
                        gap_kinds = parsed_review.gap_kinds or ["source_rule"] * len(
                            parsed_review.missing
                        )
                        for missing, kind in zip(parsed_review.missing, gap_kinds, strict=True):
                            normalized = " ".join(missing.split()).casefold()
                            if normalized in unknown_descriptions or any(
                                _requirement_labels_match(missing, description)
                                for description in unknown_descriptions
                            ):
                                continue
                            kept_missing.append(missing)
                            kept_gap_kinds.append(kind)
                        kept_checks = [
                            check for check in parsed_review.checks if check not in unknown_checks
                        ]
                        parsed_review = parsed_review.model_copy(
                            update={
                                "checks": kept_checks,
                                "missing": kept_missing,
                                "gap_kinds": kept_gap_kinds,
                                **(
                                    {
                                        "complete": not kept_missing
                                        and requirement_ids.issubset(
                                            {
                                                check.requirement_id
                                                for check in kept_checks
                                                if check.supported and check.evidence
                                            }
                                        ),
                                        "partial_answer": parsed_review.partial_answer
                                        if kept_missing
                                        else None,
                                    }
                                    if isinstance(parsed_review, CoverageVerdict)
                                    else {"partial_answer": parsed_review.partial_answer}
                                ),
                            }
                        )
                for check in parsed_review.checks:
                    for quote in check.evidence:
                        quote.chunk_id = source_ids.get(quote.chunk_id, quote.chunk_id)
                ranges_valid = parsed_review.resolve_source_ranges(budgeted)
                if ranges_valid and requirement_ids:
                    parsed_review = _guard_review_fulfillment(
                        parsed_review, requirements, budgeted, inputs.query
                    )
                if delta_review and requirement_ids:
                    verdict = proof_map.merge_delta(parsed_review, reviewing, budgeted, records)
                elif delta_review:
                    verdict = CoverageVerdict.model_validate(parsed_review.model_dump())
                else:
                    assert isinstance(parsed_review, CoverageVerdict)
                    verdict = parsed_review
                    if requirement_ids:
                        proof_map.remember_gaps(verdict.missing, verdict.checks)
                        proof_map.observe_review_checks(verdict.checks, budgeted, records)
                unfulfilled = [
                    facet
                    for check in verdict.checks
                    if check.fulfillment == "partial"
                    for facet in (check.unresolved_facets or [check.description])
                    if facet.strip()
                ]
                if unfulfilled:
                    unresolved_missing = list(dict.fromkeys([*verdict.missing, *unfulfilled]))[:12]
                    verdict = verdict.model_copy(
                        update={
                            "complete": False,
                            "missing": unresolved_missing,
                            "gap_kinds": ["source_rule"] * len(unresolved_missing),
                        }
                    )
                diagnostics["proof_map"] = proof_map.diagnostics()
                # Preserve the established successful review/generation payloads.
                # Only an otherwise incomplete verdict with *every* source check
                # proven can ask whether the remaining gaps are personal inputs.
                closed_verdict = verdict.model_copy(
                    update={"complete": True, "missing": [], "gap_kinds": []}
                )
                if (
                    authoritative_compatibility
                    and not verdict.complete
                    and verdict.missing
                    and ranges_valid
                    and closed_verdict.validates(groups, budgeted, requirement_ids)
                    and all(
                        check.fulfillment == "full" and not check.unresolved_facets
                        for check in verdict.checks
                    )
                ):
                    previous_usage = result.usage
                    result.usage = ChatUsage(None, None)
                    diagnostics["phase"] = "input_review"
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
                        call_purpose="scenario_input_review",
                    )
                    result.usage = _add_usage(previous_usage, input_review.usage)
                    if input_review.finish_reason not in {None, "stop", "completed", "end_turn"}:
                        diagnostics["status"] = "input_review_incomplete"
                        if partial_checkpoint is not None:
                            _restore_partial_checkpoint(
                                result,
                                diagnostics,
                                partial_checkpoint,
                                stop_reason="input_review_incomplete",
                            )
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
                        verdict = closed_verdict.model_copy(
                            update={"missing_inputs": [*verdict.missing_inputs, *verdict.missing]}
                        )
                # Keep quotes internal: candidate trace opt-out must not leak source
                # text through the verifier's diagnostic payload.
                full_coverage_validated = (
                    ranges_valid
                    and verdict.validates(groups, budgeted, requirement_ids)
                    and all(
                        check.fulfillment == "full" and not check.unresolved_facets
                        for check in verdict.checks
                    )
                )
                verdict.retain_answerable_scopes()
                _limit_numeric_partial_scope(verdict, mandatory_partial_ids, inputs.query)
                partial_scope_validated = ranges_valid and verdict.partial_validates(
                    budgeted, requirement_ids, mandatory_partial_ids
                )
                last_verdict = verdict
                last_partial_scope_validated = partial_scope_validated
                diagnostics["coverage"] = {
                    "complete": verdict.complete,
                    "missing": verdict.missing,
                    "gap_kinds": verdict.gap_kinds or ["source_rule"] * len(verdict.missing),
                    "gap_kinds_defaulted": verdict._gap_kinds_defaulted,
                    "missing_inputs": verdict.missing_inputs,
                    "checks": [
                        {
                            "query_index": check.query_index,
                            "supported": check.supported,
                            "fulfillment": check.fulfillment,
                            "unresolved_facets": check.unresolved_facets,
                            "answerable_scope": check.answerable_scope,
                            "needs_adjacent_context": check.needs_adjacent_context,
                            "chunk_ids": [item.chunk_id for item in check.evidence],
                            "source_ranges": [item.source_range() for item in check.evidence],
                        }
                        for check in verdict.checks
                    ],
                    "quotes_validated": full_coverage_validated,
                    "source_ranges_validated": ranges_valid,
                    "full_coverage_validated": full_coverage_validated,
                    "partial_scope_validated": partial_scope_validated,
                }
                protocol_incomplete_ids = [
                    check.requirement_id
                    for check in verdict.checks
                    if check.requirement_id
                    and any(
                        "identity protocol error" in missing.casefold()
                        and bool(
                            re.search(
                                rf"\b{re.escape(check.requirement_id)}\b",
                                missing,
                                re.IGNORECASE,
                            )
                        )
                        for missing in verdict.missing
                    )
                ]
                if protocol_incomplete_ids:
                    diagnostics["review_protocol_errors"] = [
                        {
                            "reason": "review_identity_incomplete",
                            "affected_ids": protocol_incomplete_ids,
                        }
                    ]
                for check, payload in zip(
                    verdict.checks, diagnostics["coverage"]["checks"], strict=True
                ):
                    payload.update(
                        requirement_id=check.requirement_id, description=check.description
                    )
                diagnostics["requirement_progress"] = _requirement_progress(
                    verdict,
                    focused_ids=diagnostics.get("focused_requirement_ids") or [],
                    adjacent_queries=diagnostics.get("adjacent_queries") or [],
                    attempts=diagnostics.get("requirement_attempts") or [],
                    stop_reason=diagnostics.get("stop_reason"),
                )
                if partial_scope_validated:
                    checkpoint_diagnostics = deepcopy(diagnostics)
                    _store_partial_answer(checkpoint_diagnostics, verdict)
                    checkpoint = EvidenceRepairResult([], None, checkpoint_diagnostics)
                    _handoff_reviewed_proof(
                        checkpoint,
                        verdict,
                        budgeted,
                        groups,
                        requirement_ids,
                        decisions,
                        mandatory_partial_ids,
                    )
                    if checkpoint.decision is not None:
                        partial_checkpoint = checkpoint
                if full_coverage_validated:
                    break
                # A follow-up needs retrieval and another coverage review. Do not
                # spend the last seconds starting work that cannot finish while a
                # validated, useful partial answer is ready for generation.
                if partial_scope_validated and schedule.search_deadline - monotonic() < 6:
                    diagnostics["stop_reason"] = "insufficient_followup_budget"
                    _store_partial_answer(diagnostics, verdict)
                    break
                # A rule can be supported while its applicability still needs
                # the adjoining heading. Honor the explicit continuation flag
                # on incomplete reviews instead of testing `supported` alone.
                recoverable_continuation = (
                    structural_rounds < 2
                    and ranges_valid
                    and bool(verdict.missing)
                    and getattr(retrieval, "supports_adjacent_retrieval", False) is True
                    and any(
                        (check.needs_adjacent_context or (not check.supported and check.evidence))
                        and check.evidence
                        and (requirement_ids or 0 <= check.query_index < len(raw_groups))
                        and any(
                            item.chunk_id == str(chunk.chunk_id)
                            for item in check.evidence
                            for group in raw_groups
                            for chunk in group
                        )
                        and any(
                            (
                                check.requirement_id or f"query-{check.query_index}",
                                chunk.chunk_id,
                            )
                            not in attempted_adjacent
                            for item in check.evidence
                            for group in raw_groups
                            for chunk in group
                            if item.chunk_id == str(chunk.chunk_id)
                        )
                        for check in verdict.checks
                    )
                )
                missing_core_ids = _unsupported_requirement_keys(verdict, requirement_ids)
                missing_core_ids.difference_update(protocol_incomplete_ids)
                focused_already = set(diagnostics.get("focused_requirement_ids") or [])
                missing_rule_retry = (
                    ranges_valid
                    and bool(verdict.missing)
                    and bool(missing_core_ids - focused_already)
                    and semantic_rounds < max_followup_rounds
                )
                if (
                    partial_scope_validated
                    and not recoverable_continuation
                    and not missing_rule_retry
                ):
                    _store_partial_answer(diagnostics, verdict)
                    break
                if (
                    protocol_incomplete_ids
                    and not recoverable_continuation
                    and not missing_rule_retry
                ):
                    diagnostics["status"] = "review_incomplete"
                    diagnostics["stop_reason"] = "review_identity_protocol_error"
                    return result
                diagnostics["status"] = "coverage_incomplete"
                if (
                    (semantic_rounds >= max_followup_rounds and not recoverable_continuation)
                    or verdict.complete
                    or not verdict.missing
                ):
                    diagnostics["requirement_progress"]["stop_reason"] = (
                        "repair_followup_limit"
                        if semantic_rounds >= max_followup_rounds
                        else "coverage_validation_failed"
                    )
                    _mark_unattempted_budget(diagnostics["requirement_progress"])
                    if partial_checkpoint is not None:
                        _restore_partial_checkpoint(
                            result,
                            diagnostics,
                            partial_checkpoint,
                            stop_reason=diagnostics["requirement_progress"]["stop_reason"],
                        )
                    return result
                if not round_index:
                    diagnostics["initial_coverage"] = diagnostics["coverage"]
                diagnostics.setdefault("coverage_rounds", []).append(diagnostics["coverage"])
                # Carry only exactly cited, confirmed evidence into the next
                # round. Repeatedly carrying every earlier search hit crowds out
                # newly found governing passages and makes resolved gaps recur.
                sources = {str(c.chunk_id): _quote_tokens(c.content) for c in budgeted}
                confirmed_checks = [
                    check
                    for check in verdict.checks
                    if check.supported
                    and check.evidence
                    and all(
                        item.chunk_id in sources
                        and _contains_quote(sources[item.chunk_id], item.quote)
                        for item in check.evidence
                    )
                ]
                confirmed = {q.chunk_id for check in confirmed_checks for q in check.evidence}
                structural_anchors = {
                    q.chunk_id
                    for check in verdict.checks
                    if check.evidence and (check.needs_adjacent_context or not check.supported)
                    for q in check.evidence
                    if q.chunk_id in sources and _contains_quote(sources[q.chunk_id], q.quote)
                }
                retained_proof = confirmed | structural_anchors
                groups = [
                    [c for c in group if str(c.chunk_id) in retained_proof] for group in groups
                ]
                selected = [c for c in budgeted if str(c.chunk_id) in retained_proof]
                # Read a missing rule's immediate source neighbourhood before
                # asking for another wording. Neighbours undergo the same search,
                # source policy, reranking and admission; they inherit no scores.
                adjacent_requests = {}
                if (
                    structural_rounds < 2
                    and getattr(retrieval, "supports_adjacent_retrieval", False) is True
                ):
                    for check in verdict.checks:
                        i = check.query_index
                        if (
                            not (
                                check.needs_adjacent_context
                                or (not check.supported and check.evidence)
                            )
                            or not check.evidence
                            or not ranges_valid
                            or (not requirement_ids and not 0 <= i < len(raw_groups))
                        ):
                            continue
                        # The reviewer can cite previously admitted context in
                        # addition to this round's search results. Those spans
                        # are already in the scoped review and may be the best
                        # adjacency anchor for a clipped governing heading.
                        anchor_context = [*budgeted, *(c for group in raw_groups for c in group)]
                        originals = {str(c.chunk_id): c.chunk_id for c in anchor_context}
                        anchors = list(
                            dict.fromkeys(
                                [
                                    *(
                                        originals[q.chunk_id]
                                        for q in check.evidence
                                        if q.chunk_id in originals
                                        and (
                                            check.requirement_id or f"query-{check.query_index}",
                                            originals[q.chunk_id],
                                        )
                                        not in attempted_adjacent
                                    ),
                                ]
                            )
                        )[:4]
                        if check.needs_adjacent_context and anchors:
                            # A schedule heading normally precedes its clipped
                            # rate row. If the same search already found an
                            # earlier passage in that work, anchor there too;
                            # this avoids walking only toward later-year bands.
                            for anchor in list(anchors):
                                cited = next(
                                    (chunk for chunk in anchor_context if chunk.chunk_id == anchor),
                                    None,
                                )
                                if cited is None:
                                    continue
                                preceding = max(
                                    (
                                        chunk
                                        for group in raw_groups
                                        for chunk in group
                                        if chunk.document_id == cited.document_id
                                        and chunk.chunk_index < cited.chunk_index
                                    ),
                                    key=lambda chunk: chunk.chunk_index,
                                    default=None,
                                )
                                if preceding is not None and preceding.chunk_id not in anchors:
                                    anchors.append(preceding.chunk_id)
                                if len(anchors) >= 4:
                                    break
                            # A clipped heading can make the reviewer cite a
                            # secondary source even when the top search hit is
                            # from the governing work. Complete one nearby
                            # passage from a distinct retrieved document too.
                            cited_documents = {
                                chunk.document_id
                                for chunk in anchor_context
                                if chunk.chunk_id in anchors
                            }
                            for group in raw_groups:
                                if len(anchors) >= 4:
                                    break
                                alternative = next(
                                    (
                                        chunk
                                        for chunk in group
                                        if chunk.document_id not in cited_documents
                                        and chunk.chunk_id not in anchors
                                    ),
                                    None,
                                )
                                if alternative is not None:
                                    anchors.append(alternative.chunk_id)
                                    break
                        if anchors:
                            # The reviewer may cite a passage found by another
                            # route. Search the actual missing topic, rather than
                            # inheriting an unrelated route's wording or year.
                            query = (
                                check.description.strip()
                                or verdict.missing[
                                    min(len(adjacent_requests), len(verdict.missing) - 1)
                                ]
                            )[:500]
                            # A reviewer describes gaps in the response language. The
                            # neighbouring source may use another language, and the
                            # admission gate still needs a query aligned with its text.
                            # Reuse a planned, requirement-bound query in that source
                            # language rather than issuing the English description.
                            evidence_ids = {item.chunk_id for item in check.evidence}
                            source_languages = {
                                detect_language(chunk.content).primary_language
                                for chunk in anchor_context
                                if str(chunk.chunk_id) in evidence_ids
                            }
                            requirement_key = check.requirement_id or f"query-{check.query_index}"
                            if detect_language(query).primary_language not in source_languages:
                                for planned_query in queries[:max_initial_queries]:
                                    if (
                                        requirement_key
                                        in query_requirement_ids.get(planned_query, [])
                                        and detect_language(planned_query).primary_language
                                        in source_languages
                                    ):
                                        query = planned_query
                                        break
                            adjacent_requests[query] = list(
                                dict.fromkeys([*adjacent_requests.get(query, []), *anchors])
                            )[:4]
                            query_requirement_ids[query] = list(
                                dict.fromkeys(
                                    [*query_requirement_ids.get(query, []), requirement_key]
                                )
                            )
                            attempted_adjacent.update(
                                (requirement_key, anchor) for anchor in anchors
                            )
                        if len(adjacent_requests) >= 2:
                            break
                if adjacent_requests:
                    pending_queries = list(adjacent_requests)[:2]
                    structural_rounds += 1
                    diagnostics["structural_completion_rounds"] = structural_rounds
                    queries.extend(pending_queries)
                    pending_routes = dict.fromkeys(pending_queries, "adjacent")
                    diagnostics.setdefault("adjacent_queries", []).extend(pending_queries)
                    continue
                untried_missing = missing_core_ids - focused_already
                if not untried_missing and partial_scope_validated:
                    _store_partial_answer(diagnostics, verdict)
                    break
                if remaining_followup_queries <= 0:
                    diagnostics["requirement_progress"] = {
                        **(diagnostics.get("requirement_progress") or {}),
                        "stop_reason": "followup_query_allowance_exhausted",
                    }
                    _mark_unattempted_budget(diagnostics["requirement_progress"])
                    if partial_checkpoint is not None:
                        _restore_partial_checkpoint(
                            result,
                            diagnostics,
                            partial_checkpoint,
                            stop_reason="followup_query_allowance_exhausted",
                        )
                    return result
                if release_read_transaction is not None:
                    await release_read_transaction()
                previous_usage = result.usage
                result.usage = ChatUsage(None, None)
                semantic_rounds += 1
                diagnostics["phase"] = "focused_planning"
                followup = await _validated_completion(
                    llm,
                    [
                        ChatMessage(role=ChatRole.SYSTEM, content=trusted_context + focused_prompt),
                        ChatMessage(
                            role=ChatRole.USER,
                            content=json.dumps(
                                {
                                    "question": inputs.query,
                                    "missing_requirements": (
                                        [
                                            {
                                                "requirement_id": item.requirement_id,
                                                "description": item.description,
                                            }
                                            for item in requirements
                                            if item.requirement_id in untried_missing
                                        ]
                                        if requirement_ids
                                        else verdict.missing
                                    ),
                                    "missing_gaps": verdict.missing,
                                    "supported_requirements": [
                                        {
                                            "requirement_id": check.requirement_id,
                                            "description": check.description,
                                        }
                                        for check in confirmed_checks
                                        if check.requirement_id
                                    ],
                                    "previous_queries": queries,
                                    "discovery_excerpts": _discovery_excerpts(
                                        verdict, raw_groups, budgeted
                                    ),
                                    "indexed_headings": _indexed_headings(raw_groups, budgeted),
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
                    call_purpose="recovery_planning",
                )
                result.usage = _add_usage(previous_usage, followup.usage)
                if followup.finish_reason not in {None, "stop", "completed", "end_turn"}:
                    if partial_scope_validated:
                        _store_partial_answer(diagnostics, verdict)
                        break
                    if partial_checkpoint is not None:
                        _restore_partial_checkpoint(
                            result,
                            diagnostics,
                            partial_checkpoint,
                            stop_reason="focused_plan_incomplete",
                        )
                    return result
                followup_plan = _SearchPlan.model_validate_json(followup.content)
                seen_queries = {" ".join(q.split()).casefold() for q in queries}
                pending_queries = []
                for entry in followup_plan.queries:
                    query = entry.query.strip()
                    key = " ".join(query.split()).casefold()
                    if key in seen_queries:
                        diagnostics["duplicate_focused_queries_skipped"] = (
                            diagnostics.get("duplicate_focused_queries_skipped", 0) + 1
                        )
                        continue
                    seen_queries.add(key)
                    bound_ids = [
                        requirement_id
                        for requirement_id in entry.requirement_ids
                        if requirement_id in untried_missing
                    ]
                    if requirement_ids and not bound_ids:
                        diagnostics.setdefault("unbound_focused_queries_skipped", []).append(
                            {
                                "query": query,
                                "supplied_requirement_ids": list(entry.requirement_ids),
                            }
                        )
                        continue
                    pending_queries.append(query)
                    query_requirement_ids[query] = bound_ids
                pending_queries = pending_queries[
                    : min(remaining_followup_queries, 2)
                    if work is not None and work.execution_policy == "adaptive_v1"
                    else remaining_followup_queries
                ]
                if not pending_queries or any(not q or len(q) > 500 for q in pending_queries):
                    diagnostics["requirement_progress"] = {
                        **(diagnostics.get("requirement_progress") or {}),
                        "stop_reason": "no_new_focused_query",
                    }
                    _mark_unattempted_budget(diagnostics["requirement_progress"])
                    if partial_scope_validated:
                        _store_partial_answer(diagnostics, verdict)
                        break
                    if partial_checkpoint is not None:
                        _restore_partial_checkpoint(
                            result,
                            diagnostics,
                            partial_checkpoint,
                            stop_reason="no_new_focused_query",
                        )
                    return result
                queries.extend(pending_queries)
                remaining_followup_queries -= len(pending_queries)
                pending_routes = dict.fromkeys(pending_queries, "focused")
                diagnostics.setdefault("focused_queries", []).extend(pending_queries)
            if last_verdict is None:
                diagnostics["status"] = "repair_unavailable"
                diagnostics["failure_reason"] = (
                    "deadline_exceeded"
                    if schedule.stop_reason == "exhausted_budget"
                    else "no_validated_coverage"
                )
                diagnostics["requirement_progress"] = {
                    "stop_reason": diagnostics.get("stop_reason"),
                    "attempts": diagnostics.get("requirement_attempts") or [],
                    "checks": [
                        {
                            "requirement_id": item.requirement_id,
                            "description": item.description,
                            "supported": False,
                            "fulfillment": "none",
                            "attempt_status": "budget_exhausted_before_attempt",
                        }
                        for item in requirements
                    ],
                }
                if partial_checkpoint is not None:
                    _restore_partial_checkpoint(
                        result,
                        diagnostics,
                        partial_checkpoint,
                        stop_reason=diagnostics.get("stop_reason") or "no_validated_coverage",
                    )
                return result
            verdict = last_verdict
            # Discovery context can contain old/future tables and unrelated examples.
            # Hand generation the passages actually used by the validated proof,
            # instead of every superficially relevant search hit.
            _handoff_reviewed_proof(
                result,
                verdict,
                budgeted,
                groups,
                requirement_ids,
                decisions,
                mandatory_partial_ids,
            )
            if result.decision is None and partial_checkpoint is not None:
                _restore_partial_checkpoint(
                    result,
                    diagnostics,
                    partial_checkpoint,
                    stop_reason=(diagnostics.get("requirement_progress") or {}).get("stop_reason")
                    or "validated_partial_scope",
                )
            return result
    except (ProviderError, TimeoutError, ValidationError) as exc:
        deadline_expired = isinstance(exc, TimeoutError) and (
            timeout_context.expired() or diagnostics.get("phase") == "retrieval"
        )
        bare_provider_timeout = isinstance(exc, TimeoutError) and not deadline_expired
        provider_timed_out = isinstance(exc, ProviderTimeoutError) or bare_provider_timeout
        provider_error = (
            ProviderTimeoutError(
                "Evidence provider operation timed out.",
                provider_name=llm.provider_name,
                context={"reason": "nested_provider_timeout"},
            )
            if bare_provider_timeout
            else exc
        )
        if (
            isinstance(provider_error, ProviderError)
            and provider_error.context.get("reason") == "recovery_deadline_exceeded"
        ):
            deadline_expired = True
        if deadline_expired:
            diagnostics["stop_reason"] = "recovery_deadline_exceeded"
            diagnostics["requirement_progress"] = {
                **(diagnostics.get("requirement_progress") or {}),
                "stop_reason": "recovery_deadline_exceeded",
            }
        elif provider_timed_out:
            diagnostics["stop_reason"] = "provider_timeout"
        if isinstance(provider_error, ProviderError):
            diagnostics["provider"] = provider_error.provider_name
            diagnostics["error_code"] = provider_error.code
            diagnostics["provider_context"] = dict(provider_error.context)
        if partial_checkpoint is not None:
            _restore_partial_checkpoint(
                result,
                diagnostics,
                partial_checkpoint,
                stop_reason=(
                    "recovery_deadline_exceeded"
                    if deadline_expired
                    else "provider_timeout"
                    if provider_timed_out
                    else "invalid_later_review"
                    if isinstance(exc, ValidationError)
                    else "later_review_provider_error"
                ),
            )
            return result
        if (
            deadline_expired
            and initial_decision is not None
            and initial_decision.sufficient
            and admitted_timeout_fallback
        ):
            _restore_admitted_timeout_fallback(
                result,
                diagnostics,
                initial_decision,
                admitted_timeout_fallback,
            )
            return result
        if isinstance(provider_error, ProviderError):
            result.failure = provider_error
        elif isinstance(exc, TimeoutError):
            result.failure = ProviderTimeoutError(
                "Evidence review exceeded its time limit.",
                provider_name=llm.provider_name,
                context={"reason": "recovery_deadline_exceeded"},
            )
        diagnostics["status"] = "repair_unavailable"
        diagnostics["failure_reason"] = (
            "deadline_exceeded"
            if deadline_expired
            else "provider_timeout"
            if provider_timed_out
            else "invalid_model_response"
            if isinstance(exc, ValidationError)
            else "provider_error"
        )
        if isinstance(provider_error, ProviderError):
            diagnostics["provider"] = provider_error.provider_name
            diagnostics["error_code"] = provider_error.code
            if provider_error.provider_name == "retrieval" or provider_error.context.get(
                "reason"
            ) in {
                "prompt_budget_exceeded",
                "embedding_identity_mismatch",
            }:
                diagnostics["failure_detail"] = provider_error.context.get("reason")
        elif isinstance(exc, ValidationError):
            diagnostics["validation_errors"] = [
                {"type": error["type"], "loc": list(error["loc"])}
                for error in exc.errors(include_input=False, include_url=False)
            ]
            diagnostics["failure_stage"] = str(diagnostics.get("phase") or "coverage_review")
            request_work = _request_work(llm)
            if request_work is not None and request_work.validation_retries:
                diagnostics["validation_failure"] = dict(request_work.validation_retries[-1])
        request_work = _request_work(llm)
        if request_work is not None and request_work.validation_retries:
            # Preserve protocol cause when a correction was interrupted by its phase budget.
            diagnostics["validation_failure"] = dict(request_work.validation_retries[-1])
        return result
    finally:
        schedule.close(cancelled=diagnostics.get("stop_reason") == "recovery_deadline_exceeded")
        _RECOVERY_SCHEDULE.reset(schedule_token)
        # Coverage can time out after successful searches, before it builds the
        # progress summary. Preserve actual work on every exit, including provider
        # failures; a missing verdict must not masquerade as an unattempted search.
        if "requirement_attempts" in diagnostics:
            diagnostics["requirement_progress"] = {
                **(diagnostics.get("requirement_progress") or {}),
                "attempts": deepcopy(diagnostics["requirement_attempts"]),
            }
        elapsed = monotonic() - started
        diagnostics["elapsed_seconds"] = round(elapsed, 3)
        diagnostics["elapsed_ms"] = round(elapsed * 1000)


def _unique_authority_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Avoid repeating identical relationship diagnostics for every search route."""
    return list(
        {json.dumps(record, sort_keys=True, default=str): record for record in records}.values()
    )


def _checkpoint_matches_branch(
    checkpoint: EvidenceRepairResult,
    checkpoint_key: str | None,
    branch: ContextRetrievalResult,
    records: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
) -> bool:
    """Keep prior proof when a later branch merely adds unrelated evidence.

    A checkpoint changes only when its requirements or exact proof text changes,
    or a newly discovered authority record refers to one of its source identities
    with a structured identity/scope match. Identical records do not invalidate.
    ``checkpoint_key`` remains in the signature for compatibility with focused tests.
    """
    del checkpoint_key, records
    if checkpoint.diagnostics.get("requirements", []) != requirements:
        return False
    if any(
        fresh.chunk_id == saved.chunk_id and fresh.content != saved.content
        for fresh in branch.chunks
        for saved in checkpoint.selected
    ):
        return False
    known_keys = {
        tuple(key)
        for facet in (checkpoint.diagnostics.get("proof_map") or {}).get("facets") or []
        for key in facet.get("authority_keys") or []
    }
    if not known_keys:
        known_keys = {
            _authority_dependency_key(record)
            for record in checkpoint.diagnostics.get("coverage", {}).get("authority_keys", [])
        }
    for record in branch.diagnostics.get("modifies_expansion_records") or []:
        key = _authority_dependency_key(record)
        if key in known_keys:
            continue
        if any(authority_record_affects_chunk(record, chunk) for chunk in checkpoint.selected):
            return False
    return True


def _restore_partial_checkpoint(
    result: EvidenceRepairResult,
    diagnostics: dict[str, Any],
    checkpoint: EvidenceRepairResult,
    *,
    stop_reason: str,
) -> None:
    """Restore only a checkpoint that already passed exact partial proof validation."""
    for key in ("coverage", "partial_answer", "proof_chunk_ids", "proof_map"):
        if key in checkpoint.diagnostics:
            diagnostics[key] = deepcopy(checkpoint.diagnostics[key])
    current_attempts = list(diagnostics.get("requirement_attempts") or [])
    focused_ids = list(diagnostics.get("focused_requirement_ids") or [])
    diagnostics["requirement_progress"] = {
        **deepcopy(checkpoint.diagnostics.get("requirement_progress") or {}),
        "focused_requirement_ids": focused_ids,
        "stop_reason": stop_reason,
    }
    if current_attempts:
        diagnostics["requirement_progress"]["attempts"] = current_attempts
    _mark_unattempted_budget(diagnostics["requirement_progress"])
    diagnostics["status"] = "partial_answer"
    diagnostics["partial_checkpoint_restored"] = stop_reason
    if diagnostics.get("coverage"):
        diagnostics["coverage"]["complete"] = False
        diagnostics["coverage"]["full_coverage_validated"] = False
    result.selected = list(checkpoint.selected)
    result.decision = checkpoint.decision
    result.partial_answer = deepcopy(checkpoint.partial_answer)
    result.missing_inputs = checkpoint.missing_inputs
    result.answerable_scope = deepcopy(checkpoint.answerable_scope)


def _restore_admitted_timeout_fallback(
    result: EvidenceRepairResult,
    diagnostics: dict[str, Any],
    decision: EvidenceDecision,
    selected: list[ContextChunk],
) -> None:
    """Return admitted evidence when a bounded checklist review runs out of time.

    This is intentionally narrower than an exact coverage checkpoint: it is allowed only
    for callers that classify completeness as useful but non-atomic. The answer prompt and
    final claim verifier still restrict output to facts supported by these admitted units.
    """
    pending = (
        "Complete coverage of every requested requirement and applicable source "
        "was not verified within the review budget."
    )
    partial = {
        "scope": "Facts directly supported by the admitted, source-reconciled evidence",
        "review_status": "admitted_only",
        "requirement_ids": [],
        "exclusions": [
            "Any requested compliance facet not directly established by the cited evidence."
        ],
        "pending": [pending],
        "gap_kinds": ["source_rule"],
        "supported_proof": [],
    }
    diagnostics["coverage"] = {
        "complete": False,
        "missing": [pending],
        "missing_inputs": [],
        "quotes_validated": False,
        "source_ranges_validated": False,
        "full_coverage_validated": False,
        "partial_scope_validated": False,
        "admitted_evidence_fallback": True,
        "checks": [],
    }
    diagnostics["partial_answer"] = partial
    diagnostics["requirement_progress"] = {
        **(diagnostics.get("requirement_progress") or {}),
        "stop_reason": "recovery_deadline_admitted_evidence_fallback",
    }
    diagnostics["status"] = "partial_answer"
    diagnostics["admitted_evidence_timeout_fallback"] = True
    diagnostics["proof_chunk_ids"] = [str(chunk.chunk_id) for chunk in selected]
    result.selected = list(selected)
    selected_keys = {(chunk.chunk_id, chunk.content) for chunk in selected}
    result.decision = replace(
        decision,
        sufficient=True,
        reason=None,
        winning_chunk_id=selected[0].chunk_id,
        admitted_units=tuple(
            unit
            for unit in decision.admitted_units
            if (unit.chunk_id, unit.content) in selected_keys
        ),
    )
    result.partial_answer = partial
    result.answerable_scope = _answerable_scope(
        complete=False,
        partial=partial,
        missing=(pending,),
        missing_inputs=(),
        supported_requirement_ids=[],
    )


def _reviewed_scopes(
    verdict: CoverageVerdict, partial: dict[str, Any] | None
) -> list[dict[str, Any]]:
    return [
        {
            "requirement_id": check.requirement_id,
            "supported_scope": check.answerable_scope or check.description,
            "fulfillment": check.fulfillment,
            "proof_ids": [item.chunk_id for item in check.evidence],
            "proof_spans": [
                {"chunk_id": item.chunk_id, "quote": item.quote} for item in check.evidence
            ],
            "exclusions": check.unresolved_facets,
        }
        for check in verdict.checks
        if check.supported
        and check.evidence
        and check.fulfillment in ("full", "partial")
        and not check.needs_adjacent_context
        and (not partial or check.requirement_id in partial.get("requirement_ids", []))
    ]


def _handoff_reviewed_proof(
    result: EvidenceRepairResult,
    verdict: CoverageVerdict,
    budgeted: list[ContextChunk],
    groups: list[list[ContextChunk]],
    requirement_ids: set[str],
    decisions: list[EvidenceDecision],
    mandatory_partial_ids: set[str] | dict[str, set[str]] | None = None,
) -> None:
    """Apply the same final proof validation to normal and timeout handoffs."""
    diagnostics = result.diagnostics
    partial = diagnostics.get("partial_answer")
    proof_ids = {
        item.chunk_id
        for check in verdict.checks
        if not partial or check.requirement_id in partial["requirement_ids"]
        for item in check.evidence
    }
    proof = [c for c in budgeted if str(c.chunk_id) in proof_ids]
    if not decisions or not (
        verdict.partial_validates(proof, requirement_ids, mandatory_partial_ids)
        if partial
        else verdict.validates(groups, proof, requirement_ids)
        and all(check.fulfillment == "full" for check in verdict.checks)
    ):
        diagnostics["status"] = "coverage_incomplete"
        diagnostics.pop("partial_answer", None)
        return
    diagnostics["proof_chunk_ids"] = [str(c.chunk_id) for c in proof]
    proof_by_chunk: dict[str, list[dict[str, Any]]] = {}
    for check in verdict.checks:
        for item in check.evidence:
            proof_by_chunk.setdefault(item.chunk_id, []).append(
                {
                    "requirement_id": check.requirement_id or f"query-{check.query_index}",
                    "quote": item.quote,
                    "fulfillment": check.fulfillment,
                    "supported_scope": check.answerable_scope or check.description,
                    "unresolved_facets": check.unresolved_facets,
                    "proof_unit_chunk_ids": list(dict.fromkeys(q.chunk_id for q in check.evidence)),
                }
            )
    proof = [
        replace(
            chunk,
            metadata={
                **chunk.metadata,
                "reviewed_proof": proof_by_chunk.get(str(chunk.chunk_id), []),
                "proof_unit_chunk_ids": list(
                    dict.fromkeys(
                        identity
                        for item in proof_by_chunk.get(str(chunk.chunk_id), [])
                        for identity in item["proof_unit_chunk_ids"]
                    )
                ),
            },
        )
        for chunk in proof
    ]
    result.selected = proof
    assessments = {a.chunk_id: a for d in decisions for a in d.candidate_assessments}
    units_by_id = {(u.chunk_id, u.content): u for d in decisions for u in d.admitted_units}
    result.decision = replace(
        decisions[0],
        sufficient=True,
        reason=None,
        admitted_units=tuple(
            units_by_id[(c.chunk_id, c.content)]
            for c in proof
            if (c.chunk_id, c.content) in units_by_id
        ),
        candidate_assessments=tuple(assessments.values()),
    )
    diagnostics["status"] = "partial_answer" if partial else "recovered"
    progress = diagnostics.setdefault("requirement_progress", {})
    if not progress.get("stop_reason"):
        progress["stop_reason"] = "validated_partial_scope" if partial else "coverage_complete"
    if partial:
        _mark_unattempted_budget(progress)
    result.partial_answer = partial
    result.missing_inputs = tuple(verdict.missing_inputs)
    result.answerable_scope = _answerable_scope(
        complete=not bool(partial),
        partial=partial,
        missing=tuple(verdict.missing),
        missing_inputs=result.missing_inputs,
        supported_requirement_ids=[
            check.requirement_id
            for check in verdict.checks
            if check.supported
            and check.requirement_id
            and (not partial or check.requirement_id in (partial.get("requirement_ids") or []))
        ],
    )
    result.answerable_scope["reviewed_scopes"] = _reviewed_scopes(verdict, partial)
    diagnostics["answerable_scope"] = result.answerable_scope


def _coverage_diagnostics(verdict: CoverageVerdict, validated: bool) -> dict[str, Any]:
    validated = validated and all(check.fulfillment == "full" for check in verdict.checks)
    return {
        "complete": verdict.complete and validated,
        "missing": verdict.missing,
        "missing_inputs": verdict.missing_inputs,
        "quotes_validated": validated,
        "source_ranges_validated": validated,
        "full_coverage_validated": validated,
        "partial_scope_validated": False,
        "checks": [
            {
                "requirement_id": c.requirement_id,
                "description": c.description,
                "supported": c.supported,
                "fulfillment": c.fulfillment,
                "unresolved_facets": c.unresolved_facets,
                "answerable_scope": c.answerable_scope,
                "chunk_ids": [q.chunk_id for q in c.evidence],
                "source_ranges": [q.source_range() for q in c.evidence],
            }
            for c in verdict.checks
        ],
    }


def _answerable_scope(
    *,
    complete: bool,
    partial: dict[str, Any] | None,
    missing: tuple[str, ...] | list[str],
    missing_inputs: tuple[str, ...] | list[str],
    supported_requirement_ids: list[str] | None = None,
) -> dict[str, Any]:
    supported = list(dict.fromkeys(item for item in (supported_requirement_ids or []) if item))
    if not supported and partial and not complete:
        supported = [
            str(item.get("requirement_id"))
            for item in partial.get("scope") or []
            if isinstance(item, dict) and item.get("requirement_id")
        ]
    return {
        "complete": complete,
        "partial": bool(partial) and not complete,
        "unresolved_facets": [] if complete else list(missing),
        "missing_inputs": list(missing_inputs),
        "supported_requirement_ids": supported,
    }


def _unsupported_requirement_keys(verdict: CoverageVerdict, requirement_ids: set[str]) -> set[str]:
    """Stable IDs for required rules with unresolved source obligations."""
    keys: set[str] = set()
    for check in verdict.checks:
        if check.supported and check.evidence and check.fulfillment == "full":
            continue
        if requirement_ids:
            if check.requirement_id in requirement_ids:
                keys.add(check.requirement_id)
            continue
        keys.add(check.requirement_id or f"query-{check.query_index}")
    return keys


def _store_partial_answer(diagnostics: dict[str, Any], verdict: CoverageVerdict) -> None:
    if verdict.partial_answer is None:
        return
    diagnostics["partial_answer"] = verdict.partial_answer.model_dump()
    diagnostics["partial_answer"]["scope"] = [
        {
            "requirement_id": check.requirement_id,
            "description": (check.answerable_scope or check.description),
        }
        for check in verdict.checks
        if check.requirement_id in verdict.partial_answer.requirement_ids
    ]
    diagnostics["partial_answer"]["pending"] = verdict.missing
    diagnostics["partial_answer"]["gap_kinds"] = diagnostics["coverage"]["gap_kinds"]
    diagnostics["partial_answer"]["supported_proof"] = [
        {
            "requirement_id": check.requirement_id,
            "description": (check.answerable_scope or check.description),
            "source_ids": [item.chunk_id for item in check.evidence],
        }
        for check in verdict.checks
        if check.requirement_id in verdict.partial_answer.requirement_ids and check.evidence
    ]


def _requirement_progress(
    verdict: CoverageVerdict,
    *,
    focused_ids: list[str],
    adjacent_queries: list[str],
    attempts: list[dict[str, Any]] | None = None,
    stop_reason: str | None = None,
) -> dict[str, Any]:
    attempts = list(attempts or [])
    routes_by_requirement: dict[str, list[str]] = {}
    for attempt in attempts:
        for requirement_id in attempt.get("requirement_ids") or []:
            routes_by_requirement.setdefault(requirement_id, []).append(
                str(attempt.get("route") or "search")
            )
    return {
        "focused_requirement_ids": list(focused_ids),
        "adjacent_queries": list(adjacent_queries),
        "attempts": attempts,
        "stop_reason": stop_reason,
        "checks": [
            {
                "requirement_id": check.requirement_id or f"query-{check.query_index}",
                "description": check.description,
                "supported": check.supported,
                "fulfillment": check.fulfillment,
                "unresolved_facets": check.unresolved_facets,
                "answerable_scope": check.answerable_scope,
                "needs_adjacent_context": check.needs_adjacent_context,
                "discovered_ids": [item.chunk_id for item in check.evidence],
                "routes_attempted": routes_by_requirement.get(
                    check.requirement_id or f"query-{check.query_index}", []
                ),
                "attempt_status": (
                    "executed"
                    if routes_by_requirement.get(
                        check.requirement_id or f"query-{check.query_index}", []
                    )
                    else "not_needed"
                    if check.supported
                    else "unattempted"
                ),
                "route": (
                    routes_by_requirement.get(
                        check.requirement_id or f"query-{check.query_index}", ["search"]
                    )[-1]
                ),
            }
            for check in verdict.checks
        ],
    }


def _mark_unattempted_budget(progress: dict[str, Any]) -> None:
    for check in progress.get("checks") or []:
        if isinstance(check, dict) and check.get("attempt_status") == "unattempted":
            check["attempt_status"] = "budget_exhausted_before_attempt"


def _indexed_headings(groups: list[list[ContextChunk]], context: list[ContextChunk]) -> list[str]:
    headings: list[str] = []
    seen: set[str] = set()
    for chunk in [*context, *[item for group in groups for item in group]]:
        path = chunk.metadata.get("heading_path")
        title = chunk.metadata.get("section_title")
        label = " / ".join(str(part) for part in path) if isinstance(path, list) and path else title
        if not isinstance(label, str) or not label.strip() or label in seen:
            continue
        seen.add(label)
        headings.append(label.strip())
        if len(headings) == 12:
            break
    return headings
