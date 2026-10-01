"""Typed production execution artifacts shared by chat, evaluation and diagnostics."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.schemas.message import TerminalOutcome
from app.modules.conversations.turn_resolution import RequestScope


class ExecutionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TurnIntent(ExecutionContract):
    task: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    scope: RequestScope
    modality: Literal["text"] = "text"
    periods: list[dict[str, Any]] = Field(default_factory=list)
    source_policy: str
    comparison_requested: bool = False
    overview_requested: bool = False
    calculation_requested: bool = False
    applicability_requested: bool = False

    @classmethod
    def from_scope(
        cls,
        scope: RequestScope,
        *,
        source_policy: str | None = None,
        comparison_requested: bool = False,
        overview_requested: bool = False,
        calculation_requested: bool = False,
        applicability_requested: bool = False,
    ) -> TurnIntent:
        return cls(
            task=str(scope.task_kind),
            scope=scope,
            inputs={
                "explicit_inputs": list(scope.explicit_inputs),
                "stipulated_facts": [v.model_dump(mode="json") for v in scope.stipulated_facts],
            },
            periods=[v.model_dump(mode="json") for v in scope.requested_periods],
            source_policy=source_policy or scope.source_restriction,
            comparison_requested=comparison_requested,
            overview_requested=overview_requested,
            calculation_requested=calculation_requested,
            applicability_requested=applicability_requested,
        )


class Requirement(ExecutionContract):
    requirement_id: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=1000)
    origin: Literal[
        "explicit_user_request", "necessary_applicability", "optional_corroboration"
    ] = "necessary_applicability"
    materiality: Literal[
        "governing_applicability", "central_rule", "adjacent_rule", "secondary_detail"
    ]

    assigned_scope: dict[str, Any] = Field(default_factory=dict)
    required: bool = True

    # Internal only: stable requirement IDs governing this particular claim.
    depends_on: list[str] = Field(default_factory=list, max_length=12)
    task_kind: Literal["rule_lookup", "personal_eligibility", "calculation", "comparison"] = (
        "rule_lookup"
    )

    @model_validator(mode="before")
    @classmethod
    def accept_pre_materiality_plans(cls, value: Any) -> Any:
        """Keep stored/test plans readable while making materiality required on the wire."""
        if not isinstance(value, dict) or value.get("materiality"):
            return value
        compatible = dict(value)
        origin = compatible.get("origin", "necessary_applicability")
        compatible["materiality"] = {
            "optional_corroboration": "secondary_detail",
        }.get(origin, "central_rule")
        return compatible

    @property
    def id(self) -> str:
        return self.requirement_id

    @property
    def text(self) -> str:
        return self.description

    @property
    def dependencies(self) -> list[str]:
        return self.depends_on

    @model_validator(mode="after")
    def retain_optional_origin(self) -> Requirement:
        if self.origin == "optional_corroboration":
            object.__setattr__(self, "required", False)
        return self


class RequirementGraph(ExecutionContract):
    requirements: list[Requirement] = Field(default_factory=list)

    @classmethod
    def from_coverage(
        cls, coverage: dict[str, Any], scope: RequestScope | None = None
    ) -> RequirementGraph:
        return cls(
            requirements=[
                Requirement(
                    requirement_id=str(row.get("requirement_id") or f"R{i + 1}"),
                    description=str(
                        row.get("text") or row.get("description") or row.get("requirement") or ""
                    ),
                    origin=row.get("origin") or "necessary_applicability",
                    materiality=row.get("materiality")
                    or (
                        "secondary_detail"
                        if row.get("origin") == "optional_corroboration"
                        else "central_rule"
                    ),
                    required=row.get("required") is not False
                    and row.get("origin") != "optional_corroboration",
                    depends_on=[
                        str(v) for v in (row.get("dependencies") or row.get("depends_on") or [])
                    ],
                    assigned_scope=dict(
                        row.get("assigned_scope")
                        or row.get("scope")
                        or (scope.model_dump(mode="json") if scope is not None else {})
                    ),
                )
                for i, row in enumerate(coverage.get("requirements") or [])
                if isinstance(row, dict)
            ]
        )


class EvidenceBundle(ExecutionContract):
    id: str
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    source_revision_id: str | None = None
    source_hash: str
    span_hash: str
    text: str
    char_start: int | None = None
    char_end: int | None = None
    source_char_start: int | None = None
    source_char_end: int | None = None
    local_char_start: int | None = None
    local_char_end: int | None = None
    supporting_spans: list[dict[str, Any]] = Field(default_factory=list)
    structural_relationships: list[dict[str, Any]] = Field(default_factory=list)
    quantities: list[dict[str, Any]] = Field(default_factory=list)
    applicability_proof: list[dict[str, Any]] = Field(default_factory=list)

    @classmethod
    def from_chunk(cls, chunk: ContextChunk) -> EvidenceBundle:
        meta = chunk.metadata
        return cls(
            id=str(meta.get("evidence_unit_id") or chunk.chunk_id),
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            source_revision_id=str(meta["source_revision_id"])
            if meta.get("source_revision_id")
            else None,
            source_hash=str(
                getattr(chunk, "source_chunk_hash", None)
                or meta.get("evidence_source_chunk_hash")
                or meta.get("source_chunk_hash")
                or chunk.chunk_hash
            ),
            span_hash=hashlib.sha256(chunk.content.encode()).hexdigest(),
            text=chunk.content,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            source_char_start=(chunk.char_start - int(meta.get("evidence_chunk_char_start") or 0))
            if chunk.char_start is not None
            else None,
            source_char_end=meta.get("source_chunk_char_end", chunk.char_end),
            local_char_start=meta.get("evidence_chunk_char_start", 0),
            local_char_end=meta.get("evidence_chunk_char_end", len(chunk.content)),
            supporting_spans=list(meta.get("supporting_spans") or []),
            structural_relationships=list(meta.get("source_relationships") or []),
            quantities=list(meta.get("quantities") or []),
            applicability_proof=list(meta.get("reviewed_proof") or []),
        )


class AnswerAssertion(ExecutionContract):
    id: str
    kind: str
    text: str
    requirement_ids: list[str] = Field(default_factory=list)
    bundle_ids: list[str] = Field(default_factory=list)
    calculation_references: list[str] = Field(default_factory=list)
    verdict: str
    reason: str | None = None
    verification_method: str | None = None
    deterministic_checks: dict[str, Any] = Field(default_factory=dict)


class FinalizationResult(ExecutionContract):
    intent: TurnIntent
    requirements: RequirementGraph
    evidence: list[EvidenceBundle]
    verified_assertions: list[AnswerAssertion]
    limitations: list[dict[str, Any]]
    rejected_attempts: list[AnswerAssertion]
    terminal: TerminalOutcome
    fatal_event: dict[str, Any] | None = None
    recovery_stop: str | None = None
    correction_attempts: list[dict[str, Any]] = Field(default_factory=list)

    def public_projection(self) -> dict[str, Any]:
        return {
            "version": "message.execution.v1",
            "verified_assertion_ids": [a.id for a in self.verified_assertions],
            "attempted_assertions": len(self.verified_assertions) + len(self.rejected_attempts),
            "rejected_assertions": len(self.rejected_attempts),
            "recovery_stop": self.recovery_stop,
            "terminal": self.terminal.model_dump(mode="json"),
            "normalized_scope": self.intent.scope.model_dump(mode="json"),
            "admitted_proof": [
                {"bundle_id": b.id, "chunk_id": str(b.chunk_id), "span_hash": b.span_hash}
                for b in self.evidence
            ],
        }


class PreparedExecution(Protocol):
    @property
    def selected(self) -> list[ContextChunk]: ...

    @property
    def retrieval_diagnostics(self) -> dict[str, Any]: ...

    @property
    def response_policy(self) -> dict[str, Any]: ...

    @property
    def evidence_scope(self) -> dict[str, Any]: ...

    @property
    def intent(self) -> TurnIntent: ...

    @property
    def requirements(self) -> RequirementGraph: ...

    @property
    def bundles(self) -> tuple[EvidenceBundle, ...]: ...


def claim_counts(claims: list[dict[str, Any]]) -> dict[str, int]:
    factual = [c for c in claims if c.get("claim_kind") != "coverage_scope"]
    return {
        "factual": len(factual),
        **{
            name: sum(c.get("verification") == name for c in factual)
            for name in ("supported", "unverified", "unsupported")
        },
    }


def assertion_from_claim(
    claim: dict[str, Any], index: int, bundles: list[EvidenceBundle]
) -> AnswerAssertion:
    text = str(claim.get("text") or claim.get("claim") or "")
    identity = str(
        claim.get("assertion_id")
        or "A" + hashlib.sha256((str(index) + ":" + text).encode()).hexdigest()[:20]
    )
    cited = {str(e.get("chunk_id")) for e in claim.get("evidence", []) if isinstance(e, dict)}
    return AnswerAssertion(
        id=identity,
        kind=str(claim.get("claim_kind") or "factual"),
        text=text,
        requirement_ids=[str(v) for v in claim.get("requirement_ids", [])],
        bundle_ids=[b.id for b in bundles if str(b.chunk_id) in cited]
        or [str(v) for v in claim.get("proof_ids", [])],
        calculation_references=[str(v) for v in claim.get("calculation_references", [])],
        verdict=str(claim.get("verification") or "unverified"),
        reason=claim.get("verification_reason"),
        verification_method=claim.get("verification_method"),
        deterministic_checks={
            k: claim[k]
            for k in ("arithmetic", "quantity_checks", "deterministic_checks")
            if k in claim
        },
    )


def scope_limitations(policy: dict[str, Any]) -> list[dict[str, Any]]:
    scope = policy.get("answerable_scope") or {}
    return [
        *[
            {"kind": "unresolved_facet", "text": str(v)}
            for v in scope.get("unresolved_facets") or []
        ],
        *[{"kind": "missing_input", "text": str(v)} for v in scope.get("missing_inputs") or []],
        *(
            [
                {
                    "kind": "partial_scope",
                    "supported_requirement_ids": list(scope.get("supported_requirement_ids") or []),
                }
            ]
            if scope.get("partial")
            else []
        ),
    ]


def build_finalization(
    prepared: PreparedExecution,
    published: list[dict[str, Any]],
    attempted: list[dict[str, Any]],
    terminal: TerminalOutcome,
) -> FinalizationResult:
    diagnostics = prepared.retrieval_diagnostics
    repair = diagnostics.get("knowledge_repair") or {}
    bundles = list(prepared.bundles)
    attempts = [assertion_from_claim(c, i, bundles) for i, c in enumerate(attempted)]
    by_text = {a.text: a.id for a in attempts}
    verified = []
    for i, claim in enumerate(published):
        assertion = assertion_from_claim(claim, i, bundles)
        if assertion.text in by_text:
            assertion = assertion.model_copy(update={"id": by_text[assertion.text]})
        claim["assertion_id"] = assertion.id
        if assertion.verdict == "supported":
            verified.append(assertion)
    verified_ids = {a.id for a in verified}
    return FinalizationResult(
        intent=prepared.intent,
        requirements=prepared.requirements,
        evidence=bundles,
        verified_assertions=verified,
        limitations=scope_limitations(prepared.response_policy),
        rejected_attempts=[a for a in attempts if a.id not in verified_ids],
        terminal=terminal,
        fatal_event={"stage": terminal.failure_stage, "reason": terminal.reason_code}
        if terminal.outcome in {"timed_out", "verification_failed"}
        else None,
        recovery_stop=repair.get("stop_reason")
        or (repair.get("requirement_progress") or {}).get("stop_reason"),
        correction_attempts=(
            [
                {
                    "kind": "shape",
                    "status": (diagnostics.get("answer_draft") or {}).get("shape_correction"),
                }
            ]
            if (diagnostics.get("answer_draft") or {}).get("shape_correction")
            else []
        )
        + (
            [{"kind": "verification", "status": diagnostics["verification_repair"].get("status")}]
            if diagnostics.get("verification_repair")
            else []
        ),
    )


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    content: str
    finish_reason: str | None
    input_tokens: int | None
    output_tokens: int | None
    prompt_version: str
    provider: str
    model: str
    metadata: dict[str, Any]
    citations: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    grounded: bool | None
    insufficient_evidence_reason: str | None
    finalization: FinalizationResult

    def persistence_payload(self) -> dict[str, Any]:
        return {
            key: getattr(self, key) for key in self.__dataclass_fields__ if key != "finalization"
        }
