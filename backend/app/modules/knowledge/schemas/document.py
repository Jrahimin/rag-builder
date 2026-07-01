"""Document API and service schemas — no FastAPI types."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.document import DocumentStatus


@dataclass(frozen=True, slots=True)
class DocumentIngestInput:
    """Framework-agnostic upload payload for :class:`DocumentService`."""

    filename: str
    content_type: str | None
    stream: AsyncIterator[bytes]


class DocumentResponse(BaseModel):
    """Serialized document metadata returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    filename: str
    content_type: str | None
    size_bytes: int
    storage_key: str
    content_sha256: str
    status: DocumentStatus
    version: int
    error_message: str | None = None
    parser_name: str | None = None
    parser_version: str | None = None
    page_count: int | None = None
    language: str | None = None
    parsed_text_storage_key: str | None = None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DocumentUploadResponse(DocumentResponse):
    """Upload response — same shape as read for Phase 1."""


class DocumentListParams(BaseModel):
    """List query parameters (mirrors router query args)."""

    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    include_deleted: bool = False
