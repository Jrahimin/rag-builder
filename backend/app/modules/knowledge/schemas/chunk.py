"""Chunk API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChunkResponse(BaseModel):
    """Serialized document chunk for list/read endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    project_id: uuid.UUID
    chunk_index: int
    content: str
    page_number: int | None
    page_start: int | None = None
    page_end: int | None = None
    char_start: int | None
    char_end: int | None
    token_count: int | None
    chunk_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ChunkListParams(BaseModel):
    """List query parameters for document chunks."""

    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
