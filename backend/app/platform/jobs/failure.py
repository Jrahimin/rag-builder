"""Stable transient/permanent classification for durable executions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as SqlTimeoutError

from app.platform.jobs.errors import JobError
from app.platform.providers.errors import ProviderError


@dataclass(frozen=True, slots=True)
class JobFailure:
    code: str
    message: str
    retryable: bool
    details: dict[str, Any]


def classify_job_failure(exc: Exception) -> JobFailure:
    """Map execution exceptions to a client-safe durable failure."""
    if isinstance(exc, ProviderError):
        return JobFailure(
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
            details={"provider": exc.provider_name, **exc.context},
        )
    if isinstance(exc, JobError):
        return JobFailure(
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
            details=dict(exc.context),
        )
    if isinstance(
        exc, (OperationalError, InterfaceError, SqlTimeoutError, TimeoutError, ConnectionError)
    ):
        return JobFailure(
            code="job_infrastructure_unavailable",
            message="Job infrastructure is temporarily unavailable.",
            retryable=True,
            details={"exception_type": type(exc).__name__},
        )
    return JobFailure(
        code="job_execution_failed",
        message="Job execution failed.",
        retryable=False,
        details={"exception_type": type(exc).__name__},
    )
