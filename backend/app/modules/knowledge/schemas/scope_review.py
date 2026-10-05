"""Bounded source-reviewed publication, independent of extraction and legal date inference."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.platform.domain.source_scope import ScopeFact


class ScopeReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_id: uuid.UUID
    source_revision_id: uuid.UUID
    source_generation: int = Field(ge=0)
    source_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    chunk_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    facts: list[ScopeFact] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def bound_review(self) -> ScopeReviewCreate:
        if len(self.model_dump_json().encode()) > 512000 or any(
            len(str(v.source_span.get("text", ""))) > 6000 for v in self.facts
        ):
            raise ValueError("Scope review exceeds its bounded source-proof envelope")
        return self


class ScopeReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    build_id: uuid.UUID
    chunk_id: uuid.UUID
    source_revision_id: uuid.UUID
    source_generation: int
    chunk_hash: str
    review_hash: str
    envelope: dict[str, Any]
    created_by: str
    created_at: datetime
