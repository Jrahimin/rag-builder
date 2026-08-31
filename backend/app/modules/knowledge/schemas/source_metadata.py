"""Contracts for immutable Knowledge-owned source metadata."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.source_metadata import (
    SourceLifecycleStatus,
    SourceRelationshipType,
    SourceRole,
)


class SourceRelationshipCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relationship_type: SourceRelationshipType
    target_revision_id: uuid.UUID
    target_provisions: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("target_provisions")
    @classmethod
    def normalize_target_provisions(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("target_provisions must not contain blank values")
        if len(set(normalized)) != len(normalized):
            raise ValueError("target_provisions must not contain duplicates")
        return normalized

    @model_validator(mode="after")
    def validate_scope_type(self) -> SourceRelationshipCreate:
        if self.target_provisions and self.relationship_type is not SourceRelationshipType.MODIFIES:
            raise ValueError("target_provisions are supported only for modifies relationships")
        return self


class SourceRevisionCreate(BaseModel):
    """Create one immutable metadata revision and its relationship edges."""

    model_config = ConfigDict(extra="forbid")

    source_group_id: uuid.UUID | None = None
    create_new_group: bool = False
    revision_number: int | None = Field(default=None, ge=1)
    revision_label: str = Field(default="Revision", min_length=1, max_length=128)
    title: str | None = Field(default=None, min_length=1, max_length=512)
    source_type: str | None = Field(default=None, max_length=128)
    published_date: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    lifecycle_status: SourceLifecycleStatus = SourceLifecycleStatus.ACTIVE
    source_role: SourceRole = SourceRole.PRIMARY
    relationships: list[SourceRelationshipCreate] = Field(default_factory=list, max_length=100)
    change_reason: str | None = Field(default=None, max_length=2000)
    activate: bool = False

    @field_validator("revision_label", "title", "source_type", "change_reason")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped

    @model_validator(mode="after")
    def validate_interval_and_group(self) -> SourceRevisionCreate:
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to must be on or after effective_from")
        if self.create_new_group and self.source_group_id is not None:
            raise ValueError("source_group_id cannot be supplied when create_new_group is true")
        edges = {(item.relationship_type, item.target_revision_id) for item in self.relationships}
        if len(edges) != len(self.relationships):
            raise ValueError("relationships must not contain duplicate edges")
        return self


class SourceRelationshipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    relationship_type: SourceRelationshipType
    target_revision_id: uuid.UUID
    target_provisions: list[str] = Field(default_factory=list)
    created_at: datetime


class SourceRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    document_id: uuid.UUID
    source_group_id: uuid.UUID
    revision_number: int
    revision_label: str
    title: str
    source_type: str | None
    published_date: date | None
    effective_from: date | None
    effective_to: date | None
    lifecycle_status: SourceLifecycleStatus
    source_role: SourceRole
    change_reason: str
    created_by: str
    content_hash: str
    created_at: datetime
    relationships: list[SourceRelationshipResponse] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SourceActivationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    document_id: uuid.UUID
    source_revision_id: uuid.UUID
    generation: int
    activated_by: str
    reason: str
    created_at: datetime


class SourceRevisionActivation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reason must not be blank")
        return stripped


class ActiveSourceResponse(BaseModel):
    document_id: uuid.UUID
    activation: SourceActivationResponse
    revision: SourceRevisionResponse


class SourceStateResponse(BaseModel):
    project_id: uuid.UUID
    generation: int
    current_generation: int
    items: list[ActiveSourceResponse]


class SourceRevisionCreateResponse(BaseModel):
    revision: SourceRevisionResponse
    activation: SourceActivationResponse | None = None
