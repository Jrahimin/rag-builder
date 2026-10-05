"""Neutral versioned source scope proof shared by producers and index integrity."""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCOPE_FACT_VERSION: Literal["scope.v2"] = "scope.v2"


def span_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ScopeFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["scope.v2"] = SCOPE_FACT_VERSION
    kind: Literal["period", "provision"]
    value: str
    legal_kind: Literal["assessment", "fiscal", "calendar"] | None = None
    start_year: int | None = Field(default=None, strict=True)
    end_year: int | None = Field(default=None, strict=True)
    period_mode: Literal["single", "range"] = "single"
    scope: Literal["mention", "governing"] = "mention"
    locality: Literal["document", "provision", "table", "span"] = "span"
    locality_id: str
    effect: Literal["unknown", "operative", "proposal", "example"] = "unknown"
    exhaustive: bool = False
    source_span: dict[str, Any]
    status: Literal["source_attested", "reviewed"] = "source_attested"
    review_provenance: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_governing(self) -> ScopeFact:
        span = self.source_span
        if (self.scope == "governing" or self.effect == "operative" or self.exhaustive) and (
            self.status != "reviewed"
            or not all(
                self.review_provenance.get(k, "").strip()
                for k in ("reviewer", "evidence_hash", "reason")
            )
            or span.get("provenance") != "exact_source_span"
            or type(span.get("char_start")) is not int
            or type(span.get("char_end")) is not int
            or span["char_start"] < 0
            or span["char_end"] <= span["char_start"]
            or span["char_end"] - span["char_start"] != len(str(span.get("text", "")))
            or not span.get("text")
            or self.review_provenance.get("evidence_hash") != span_hash(str(span.get("text", "")))
        ):
            raise ValueError("Governing facts require exact spans and review provenance")
        if (self.kind == "period" and self.legal_kind is not None) and (
            self.start_year is None or self.end_year is None or self.end_year < self.start_year
        ):
            raise ValueError("Typed periods require ordered years")
        return self


def validated_scope_envelope(metadata: dict[str, Any], content: str) -> list[ScopeFact]:
    """One representation shared by publication and consumers; invalid data is unknown."""
    if metadata.get("scope_fact_version") != SCOPE_FACT_VERSION:
        return []
    raw_facts = metadata.get("scope_facts")
    if not isinstance(raw_facts, list):
        return []
    try:
        if any(
            not isinstance(raw, dict)
            or raw.get("version") != SCOPE_FACT_VERSION
            or set(raw) != set(ScopeFact.model_fields)
            for raw in raw_facts
        ):
            return []
        facts = [ScopeFact.model_validate(raw) for raw in raw_facts]
        for fact in facts:
            quote = fact.source_span.get("text")
            if not isinstance(quote, str) or not quote or quote not in content:
                return []
        return facts
    except (ValueError, TypeError):
        return []
