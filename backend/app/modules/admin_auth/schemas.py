"""HTTP contracts for the deliberately small Super Admin auth surface."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AdminLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class CurrentAdminResponse(BaseModel):
    id: UUID
    email: str
    role: str
    last_login_at: datetime | None

    model_config = {"from_attributes": True}


class AuthenticatedAdmin(BaseModel):
    id: UUID
    email: str
    role: str
    session_id: UUID
    last_login_at: datetime | None


class AdminUserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=1024)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class AdminUserStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool


class AdminUserResponse(BaseModel):
    id: UUID
    email: str
    role: str
    is_active: bool
    last_login_at: datetime | None
    deleted_at: datetime | None
    deleted_by: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
