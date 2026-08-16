"""Operator contracts for Project AI policy and immutable revisions."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.platform.config.project_ai import (
    ConfigProvenance,
    EffectiveProjectAIConfig,
    ProjectAIConfig,
)


class ProjectAIConfigRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configuration: ProjectAIConfig
    expected_active_revision_id: uuid.UUID | None = None
    reason: str = Field(min_length=1, max_length=2000)


class ProjectAIConfigRestore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_active_revision_id: uuid.UUID | None = None
    reason: str = Field(min_length=1, max_length=2000)


class ProjectAIConfigRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    revision_number: int
    configuration_hash: str
    configuration: ProjectAIConfig
    created_by: str
    reason: str
    restored_from_revision_id: uuid.UUID | None
    created_at: datetime


class EffectiveProjectAIConfigResponse(BaseModel):
    project_id: uuid.UUID
    active_revision_id: uuid.UUID | None
    configuration_hash: str
    configuration: EffectiveProjectAIConfig
    origins: dict[str, str]
    provenance: ConfigProvenance


class ProjectOwnershipChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_current_organization_id: uuid.UUID
    target_organization_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=2000)


class ProjectOwnershipConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_current_organization_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=2000)


class ProjectOwnershipPreflight(BaseModel):
    project_id: uuid.UUID
    current_organization_id: uuid.UUID
    target_organization_id: uuid.UUID
    ownership_locked: bool
    can_reassign: bool
    resource_counts: dict[str, int]
