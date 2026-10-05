"""Canonical answer draft: constraints never substitute for factual verification."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.conversations.calculation_graph import CalculationGraph
from app.platform.providers.contracts.llm import StructuredOutput


class AnswerSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assertion_id: str | None = Field(default=None, min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=6000)
    requirement_ids: list[str] = Field(max_length=12)
    proof_ids: list[str] = Field(max_length=24)
    calculation_references: list[str] = Field(default_factory=list, max_length=32)

    def stable_id(self, position: int) -> str:
        return (
            self.assertion_id
            or "A" + hashlib.sha256((str(position) + ":" + self.text).encode()).hexdigest()[:20]
        )


class DraftNotice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["documentary_scope", "proposal_scope", "unresolved_applicability"]
    proof_ids: list[str] = Field(max_length=24)


class AnswerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["answer.draft.v1"] = "answer.draft.v1"
    segments: list[AnswerSegment] = Field(min_length=1, max_length=60)
    notices: list[DraftNotice] = Field(default_factory=list, max_length=6)
    calculations: CalculationGraph = Field(default_factory=CalculationGraph)

    @classmethod
    def contract(cls) -> StructuredOutput:
        return StructuredOutput("answer_draft_v1", cls.model_json_schema())

    @classmethod
    def instructions(cls) -> str:
        example = cls(
            segments=[
                AnswerSegment(
                    text="Approved atomic assertion",
                    requirement_ids=["R1"],
                    proof_ids=["supplied-chunk-id"],
                )
            ]
        )
        return (
            "Return ONLY one JSON object matching this schema: "
            + json.dumps(cls.model_json_schema(), ensure_ascii=False)
            + "\nComplete object example: "
            + example.model_dump_json()
            + "\nEach factual segment must reference supplied approved requirement and proof IDs. "
            "Do not put citation markers in text; the renderer assigns them. "
            "Each segment contains one indivisible factual assertion. Preserve its assertion_id "
            "during corrections. Do not invent IDs, assertions, exclusions or new proof. "
            "Use typed notices for documentary/proposal/unresolved applicability scope; never "
            "turn a limitation into a factual assertion that a proposal was never enacted. "
            "Calculation operands must reference input:key, source:key or node:key supplied in "
            "the prompt; calculations do not establish legal applicability. "
            "A calculation segment must reference exactly one node and contain only its "
            "canonical expression: addition/subtraction/multiplication/division use + - * /; "
            "minimum, maximum and ordered_brackets use operation(operand, ...) = result. "
            "Put legal applicability in a separate supported factual segment."
        )


def public_draft_diagnostics(value: Any) -> dict[str, Any] | None:
    """Public diagnostics contain no unverified assertion or provider payload."""
    if not isinstance(value, dict):
        return None
    result = {
        key: value[key]
        for key in ("version", "status", "reason")
        if isinstance(value.get(key), str) and len(value[key]) <= 100
    }
    count = value.get("candidate_count")
    if isinstance(count, int) and not isinstance(count, bool):
        result["candidate_count"] = max(0, min(60, count))
    if isinstance(value.get("shape_correction"), str) and value["shape_correction"] in {
        "completed",
        "failed",
        "rejected_assertion_or_proof_change",
    }:
        result["shape_correction"] = value["shape_correction"]
    issues = value.get("issues")
    result["issues"] = [
        {
            "type": str(item.get("type", "validation_error"))[:80],
            "path": str(item.get("path", ""))[:160],
        }
        for item in (issues if isinstance(issues, list) else [])[:12]
        if isinstance(item, dict)
    ]
    # Draft bindings are candidate references, not approved evidence. The public
    # claims and citation snapshots carry the verified references separately.
    return result


def render_verified_segments(
    segments: list[dict[str, Any]], *, supported_ids: set[str], proof_indexes: dict[str, int]
) -> str:
    """Render each verified assertion once; preserve exact useful documentary text."""
    rows = []
    seen: set[str] = set()
    rendered: set[tuple[str, tuple[str, ...]]] = set()
    for segment in segments:
        assertion_id = segment["assertion_id"]
        if assertion_id not in supported_ids:
            continue
        if assertion_id in seen:
            raise ValueError("Duplicate verified assertion ID")
        seen.add(assertion_id)
        proofs = list(dict.fromkeys(segment["proof_ids"]))
        calculation = bool(segment.get("calculation_references"))
        if (not proofs and not calculation) or any(proof not in proof_indexes for proof in proofs):
            raise ValueError("Verified assertion has an unbound proof")
        identity = (segment["text"].strip(), tuple(sorted(proofs)))
        if identity in rendered:
            continue
        rendered.add(identity)
        rows.append(
            segment["text"].strip() + "".join(f" [{proof_indexes[proof]}]" for proof in proofs)
        )
    return "\n\n".join(rows)
