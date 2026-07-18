"""Safe corpus and immutable index lifecycle API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.index_build import IndexBuildOperation, IndexBuildState


class IndexBuildResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    job_id: uuid.UUID | None
    state: IndexBuildState
    operation: IndexBuildOperation
    embedding_set_version: int
    configuration_hash: str
    corpus_fingerprint: str | None
    document_count: int
    chunk_count: int
    vector_count: int
    keyword_count: int
    manifest: dict[str, Any]
    validated_at: datetime | None
    activated_at: datetime | None
    failure_code: str | None
    failure_message: str | None
    created_at: datetime
    updated_at: datetime


class IndexBuildListResponse(BaseModel):
    items: list[IndexBuildResponse]
    active_build_id: uuid.UUID | None
    previous_build_id: uuid.UUID | None


class LifecycleJobResponse(BaseModel):
    job_id: uuid.UUID
    build_id: uuid.UUID | None = None
    created: bool


class StorageReconciliationResponse(BaseModel):
    job_id: uuid.UUID
    created: bool
