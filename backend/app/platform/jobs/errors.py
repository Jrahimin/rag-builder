"""Job processing errors."""

from __future__ import annotations

from typing import Any


class JobError(Exception):
    """Base class for job-processing failures."""

    code: str = "job_error"

    def __init__(
        self, message: str, *, retryable: bool = False, context: dict[str, Any] | None = None
    ) -> None:
        self.message = message
        self.retryable = retryable
        self.context = context or {}
        super().__init__(message)


class JobEnqueueError(JobError):
    """Failed to place a job on the queue."""

    code = "job_enqueue_error"
