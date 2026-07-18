"""Background job foundation — minimal application-facing contract.

Worker handler registration and Taskiq wiring are introduced with the first real
background job. See ``docs/architecture/background-processing.md``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

type JobProgressCallback = Callable[[str, int], Awaitable[None]]


class RetryPolicy(BaseModel):
    """Durable execution retry limit stored on :class:`JobRun`."""

    max_attempts: int = Field(default=3, ge=1)


class JobDefinition(BaseModel):
    """Description staged durably before a Taskiq delivery is attempted."""

    name: str
    project_id: uuid.UUID
    document_id: uuid.UUID | None = None
    payload_version: int = 1
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    retry: RetryPolicy = Field(default_factory=RetryPolicy)


class JobConfiguration(BaseModel):
    """Normalized, secret-free processing, indexing, and quality configuration."""

    schema_version: int = 2
    processing: dict[str, Any]
    index: dict[str, Any]
    quality: dict[str, Any]

    def digest(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class JobSubmission(BaseModel):
    """Identity returned by asynchronous product actions."""

    job_id: uuid.UUID
    created: bool


class JobQueue(ABC):
    """Executor transport contract used only by the durable outbox dispatcher."""

    @abstractmethod
    async def enqueue(self, job: JobDefinition) -> str:
        """Place a job on the queue. Returns the assigned job id."""


class DurableJobSubmitter(ABC):
    """Cross-module seam for staging and opportunistically dispatching jobs."""

    @abstractmethod
    async def stage(
        self,
        job: JobDefinition,
        configuration: JobConfiguration,
        *,
        configuration_snapshot_id: uuid.UUID | None = None,
        retry_of_job_id: uuid.UUID | None = None,
    ) -> JobSubmission:
        """Stage a job and outbox intent in the caller's transaction."""

    @abstractmethod
    async def dispatch(self, job_id: uuid.UUID) -> None:
        """Best-effort dispatch after the caller commits; never loses the intent."""
