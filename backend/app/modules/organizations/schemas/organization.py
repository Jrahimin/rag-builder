"""Pydantic schemas for the Organizations module."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class OrganizationCreate(BaseModel):
    """Payload for creating a new Organization."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "name must not be blank"
            raise ValueError(msg)
        return stripped


class OrganizationUpdate(BaseModel):
    """Partial metadata update for an Organization."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="before")
    @classmethod
    def reject_null_name(cls, data: Any) -> Any:
        if isinstance(data, dict) and "name" in data and data["name"] is None:
            msg = "name cannot be null; omit the field to leave unchanged"
            raise ValueError(msg)
        return data

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            msg = "name must not be blank"
            raise ValueError(msg)
        return stripped


class OrganizationResponse(BaseModel):
    """Serialized Organization entity returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    is_active: bool
    deleted_at: datetime | None
    deleted_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
