"""Pydantic schemas for conversations."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConversationCreate(BaseModel):
    """Payload for creating a conversation."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=255)
    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=128)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    system_prompt_version: str | None = Field(default=None, max_length=32)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class ConversationUpdate(BaseModel):
    """Partial conversation metadata update."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=255)
    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=128)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    system_prompt_version: str | None = Field(default=None, max_length=32)

    @model_validator(mode="before")
    @classmethod
    def reject_null_title(cls, data: Any) -> Any:
        if isinstance(data, dict) and "title" in data and data["title"] is None:
            msg = "title cannot be null; omit the field to leave unchanged"
            raise ValueError(msg)
        return data

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            msg = "title must not be blank"
            raise ValueError(msg)
        return stripped


class ConversationResponse(BaseModel):
    """Serialized conversation entity."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    title: str | None
    provider: str | None
    model: str | None
    temperature: float | None
    system_prompt_version: str | None
    last_message_at: datetime | None
    is_active: bool
    deleted_at: datetime | None
    deleted_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
