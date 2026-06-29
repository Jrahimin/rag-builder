"""Background job foundation — minimal application-facing contract.

Worker handler registration and Arq wiring are introduced with the first real
background job. See ``docs/architecture/background-processing.md``.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    """Lifecycle states used when job tracking is implemented."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RetryPolicy(BaseModel):
    """Retry behaviour for enqueue (single source of truth)."""

    max_attempts: int = Field(default=3, ge=1)
    initial_delay_seconds: float = Field(default=1.0, ge=0)
    max_delay_seconds: float = Field(default=300.0, ge=0)
    exponential_base: float = Field(default=2.0, ge=1)

    def delay_for_attempt(self, attempt: int) -> float:
        delay = self.initial_delay_seconds * (self.exponential_base ** (attempt - 1))
        return min(delay, self.max_delay_seconds)


class JobDefinition(BaseModel):
    """Description of a background job submitted by the API."""

    name: str
    project_id: uuid.UUID
    payload_version: int = 1
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    retry: RetryPolicy = Field(default_factory=RetryPolicy)


class JobQueue(ABC):
    """Application-facing queue contract — enqueue only."""

    @abstractmethod
    async def enqueue(self, job: JobDefinition) -> str:
        """Place a job on the queue. Returns the assigned job id."""
