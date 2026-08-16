"""Pydantic schemas for Organization API keys."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator


class ApiKeyCreate(BaseModel):
    """Payload for creating a named API key."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "name must not be blank"
            raise ValueError(msg)
        return stripped


class ApiKeyResponse(BaseModel):
    """Serialized API key metadata (never includes the secret)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    key_prefix: str
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_by: str | None = None
    rotated_from_key_id: uuid.UUID | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> str:
        return "revoked" if self.revoked_at is not None else "active"


class ApiKeySecretResponse(ApiKeyResponse):
    """API key response including the one-time secret."""

    secret: str


class ApiKeyRotate(BaseModel):
    """Replacement-first rotation; immediate revocation requires confirmation."""

    model_config = ConfigDict(extra="forbid")

    replacement_name: str | None = Field(default=None, min_length=1, max_length=64)
    revoke_old: bool = False
    confirm_immediate_revocation: bool = False

    @field_validator("replacement_name")
    @classmethod
    def strip_replacement_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("replacement_name must not be blank")
        return stripped

    @model_validator(mode="after")
    def confirm_emergency_revocation(self) -> ApiKeyRotate:
        if self.revoke_old and not self.confirm_immediate_revocation:
            raise ValueError(
                "confirm_immediate_revocation must be true when revoke_old is requested"
            )
        return self
