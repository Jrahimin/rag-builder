"""Project-scoped durable job list/detail/retry schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.job_run import JobState, JobType


class JobListParams(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    state: JobState | None = None
    job_type: JobType | None = None
    document_id: uuid.UUID | None = None


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    job_type: JobType
    state: JobState
    stage: str
    progress: int
    attempt_count: int
    max_attempts: int
    idempotency_key: str
    document_id: uuid.UUID | None
    configuration_snapshot_id: uuid.UUID
    retry_of_job_id: uuid.UUID | None
    next_attempt_at: datetime | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    failure_code: str | None
    failure_message: str | None
    failure_details: dict[str, Any] | None
    result: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class JobDetailResponse(JobResponse):
    payload: dict[str, Any]
    configuration_hash: str
    configuration_schema_version: int
    configuration: dict[str, Any]
