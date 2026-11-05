"""Immutable request semantics shared by retrieval, governance and verification."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.core.calendar import (
    ProjectCalendarPolicy as ProjectCalendarPolicy,
)
from app.core.calendar import (
    RequestedPeriod as RequestedPeriod,
)


class StipulatedFact(BaseModel):
    """User scenario stipulation, never evidence of legal membership."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    field: Literal["category", "condition"]
    value: str
    origin: Literal["user"] = "user"
    source_text: str


class RequestScope(BaseModel):
    """One every-turn scope contract. Periods are not invented snapshot dates."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str = "request.scope.v1"
    task_kind: Literal[
        "lookup", "explanation", "eligibility", "calculation", "comparison", "overview"
    ] = "lookup"
    subject: str = ""
    requested_periods: tuple[RequestedPeriod, ...] = ()
    exact_as_of: datetime | None = None
    current_reference_time: datetime | None = None
    temporal_basis: Literal["applicable_rule", "known_at"] = "applicable_rule"
    known_at_inclusive: bool = True
    jurisdiction: str | None = None
    category: str | None = None
    named_instruments: tuple[str, ...] = ()
    source_restriction: str = "project_default"
    document_id: uuid.UUID | None = None
    explicit_inputs: tuple[str, ...] = ()
    stipulated_facts: tuple[StipulatedFact, ...] = ()
    eligibility_requested: bool = False
    project_defaults: str = ""
    index_build_id: uuid.UUID | None = None
    period_ambiguous: bool = False
    period_origin: Literal["explicit", "project_calendar", "unspecified"] = "unspecified"


def request_scope_hash(scope: RequestScope | dict) -> str:
    value = scope if isinstance(scope, RequestScope) else RequestScope.model_validate(scope)
    return hashlib.sha256(value.model_dump_json().encode()).hexdigest()
