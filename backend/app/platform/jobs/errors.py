"""Job processing errors."""

from __future__ import annotations

from typing import Any


class JobError(Exception):
    """Base class for job-processing failures."""

    code: str = "job_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool = False,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.code = code or self.code
        self.retryable = retryable
        self.context = context or {}
        super().__init__(message)


class JobEnqueueError(JobError):
    """Failed to place a job on the queue."""

    code = "job_enqueue_error"


class JobLeaseLostError(JobError):
    """The worker no longer owns the durable execution lease."""

    code = "job_lease_lost"


class PermanentJobError(JobError):
    """A stable input/configuration failure that should not be retried."""

    code = "permanent_job_error"
