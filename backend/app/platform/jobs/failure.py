"""Stable transient/permanent classification for durable executions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as SqlTimeoutError

from app.platform.jobs.errors import JobError
from app.platform.providers.errors import ProviderError

# PostgreSQL marks a deadlock victim with ``40P01`` and a serialization conflict
# with ``40001``.  Neither says anything about the uploaded document: retrying the
# transaction is the prescribed recovery.  asyncpg can wrap these in its adapter
# exception, so inspect the chained database exceptions instead of depending on a
# driver-specific exception class.
_RETRYABLE_DATABASE_SQLSTATES = frozenset({"40001", "40P01"})


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
    sqlstate = _retryable_database_sqlstate(exc)
    if sqlstate is not None:
        return JobFailure(
            code="job_database_conflict",
            message="A temporary database transaction conflict occurred.",
            retryable=True,
            details={"exception_type": type(exc).__name__, "sqlstate": sqlstate},
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


def _retryable_database_sqlstate(exc: Exception) -> str | None:
    """Return a retryable PostgreSQL SQLSTATE from an exception chain, if any."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        sqlstate = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if sqlstate in _RETRYABLE_DATABASE_SQLSTATES:
            return str(sqlstate)
        if isinstance(current, DBAPIError):
            current = current.orig
        else:
            current = current.__cause__ or current.__context__
    return None
