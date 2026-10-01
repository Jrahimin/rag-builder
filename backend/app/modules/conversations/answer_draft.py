"""Canonical answer draft: constraints never substitute for factual verification."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.platform.providers.contracts.llm import StructuredOutput


class AnswerSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=6000)
    requirement_ids: list[str] = Field(max_length=12)
    proof_ids: list[str] = Field(max_length=24)


class AnswerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["answer.draft.v1"] = "answer.draft.v1"
    segments: list[AnswerSegment] = Field(min_length=1, max_length=60)

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
            "Do not invent IDs, assertions, exclusions or new proof. A nonfactual scope limitation "
            "may have empty requirement_ids and proof_ids but must add no source assertion."
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
