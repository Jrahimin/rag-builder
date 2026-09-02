"""Operator contracts for Project AI policy and immutable revisions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.platform.config.project_ai import (
    ConfigProvenance,
    EffectiveProjectAIConfig,
    InvariantState,
    ProjectAIConfig,
    ProjectAIConfigV1,
    StructuredOrigin,
    V1NormalizationResult,
    V2ProfileNormalizationResult,
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
    schema_version: int
    configuration_hash: str
    configuration: ProjectAIConfig | ProjectAIConfigV1
    created_by: str
    source: str
    reason: str
    restored_from_revision_id: uuid.UUID | None
    created_at: datetime


class GenerationModelOption(BaseModel):
    id: str
    provider: str
    model: str


class RAGProfileOption(BaseModel):
    id: str
    profile_hash: str
    certification_status: str
    selectable: bool
    recommended: bool = False
    values: dict[str, object]


class EffectiveProjectAIConfigResponse(BaseModel):
    project_id: uuid.UUID
    active_revision_id: uuid.UUID | None
    configuration_hash: str
    effective_value_hash: str
    resolution_fingerprint: str
    configuration: EffectiveProjectAIConfig
    # Resolve the deployment policy separately so clients can tell an explicit
    # Project value that happens to match the current default from a real
    # override.
    deployment_configuration: EffectiveProjectAIConfig | None = None
    origins: dict[str, str]
    structured_origins: dict[str, StructuredOrigin]
    provenance: ConfigProvenance
    invariants: InvariantState
    compatibility_warnings: list[str] = Field(default_factory=list)
    required_index_action: str = "none"
    allowed_generation_models: list[GenerationModelOption] = Field(default_factory=list)
    rag_profiles: list[RAGProfileOption] = Field(default_factory=list)
    base_profile_id: str | None = None
    custom_execution: bool = False


class ProjectAIConfigNormalizeConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_active_revision_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=2000)
    confirm: Literal[True]


class ProjectAIConfigNormalizationPreview(BaseModel):
    project_id: uuid.UUID
    source_revision_id: uuid.UUID
    source_schema_version: int
    result: V1NormalizationResult


class ProjectAIProfileNormalizationPreview(BaseModel):
    project_id: uuid.UUID
    source_revision_id: uuid.UUID
    source_schema_version: int
    result: V2ProfileNormalizationResult


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
