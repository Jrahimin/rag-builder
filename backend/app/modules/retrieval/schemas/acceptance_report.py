"""JSON-only bounded input; APIs never open caller-supplied server paths."""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def validate_public_capture(raw: dict[str, Any]) -> None:
    forbidden = {
        "operator_diagnostic",
        "password",
        "api_key",
        "authorization",
        "cookie",
        "reasoning_content",
    }

    def inspect(value: Any) -> None:
        if isinstance(value, dict):
            if any(str(k).lower() in forbidden for k in value):
                raise ValueError(
                    "Private/credential fields do not belong in public Message captures"
                )
            for v in value.values():
                inspect(v)
        elif isinstance(value, list):
            for v in value:
                inspect(v)

    inspect(raw)
    if len(json.dumps(raw, ensure_ascii=False).encode()) > 256000:
        raise ValueError("One captured Message exceeds the report bound")


class ReviewedSourceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_id: uuid.UUID
    chunk_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    quote: str = Field(min_length=1, max_length=6000)
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)


class ReviewedCaseLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=32000)
    expected: Literal["answered", "partial", "insufficient_evidence", "unresolved_authority"]
    reason: str = Field(min_length=1, max_length=2000)
    inventory_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    spans: list[ReviewedSourceSpan] = Field(default_factory=list, max_length=100)
    missing_requirements: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def require_proof_or_gap(self) -> ReviewedCaseLabel:
        if self.expected in {"answered", "partial"} and not self.spans:
            raise ValueError("Answerable labels require reviewed source spans")
        if self.expected != "answered" and not self.missing_requirements:
            raise ValueError("Limitations require named missing requirements")
        return self


class CapturedTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1, max_length=160)
    repetition: int = Field(ge=1, le=3)
    build_id: uuid.UUID
    user_message_id: uuid.UUID
    assistant_message_id: uuid.UUID
    raw_message: dict[str, Any]

    @model_validator(mode="after")
    def bound_raw(self) -> CapturedTurn:
        validate_public_capture(self.raw_message)
        return self


class ParityCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assistant_message_id: uuid.UUID
    transport: Literal["sse", "get"]
    raw_message: dict[str, Any]

    @model_validator(mode="after")
    def bound_raw(self) -> ParityCapture:
        validate_public_capture(self.raw_message)
        return self


class ObservedAcceptanceReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["acceptance.observed.v1"] = "acceptance.observed.v1"
    comparison_mode: Literal["active_candidate", "first_build"] = "active_candidate"
    compared_active_build_id: uuid.UUID | None = None
    labels: dict[str, ReviewedCaseLabel] = Field(min_length=1, max_length=100)
    turns: list[CapturedTurn] = Field(min_length=1, max_length=600)
    parity: list[ParityCapture] = Field(min_length=2, max_length=20)

    @model_validator(mode="after")
    def bound_report(self) -> ObservedAcceptanceReportCreate:
        if len(self.model_dump_json().encode()) > 8000000:
            raise ValueError("Observed report exceeds the eight MB bound")
        return self


class ObservedAcceptanceReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    build_id: uuid.UUID
    report_hash: str
    report: dict[str, Any]
