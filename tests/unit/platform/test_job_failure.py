"""Failure classification coverage for durable jobs."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import DBAPIError

from app.platform.jobs.failure import classify_job_failure

pytestmark = pytest.mark.unit


class _PostgresDeadlock(Exception):
    sqlstate = "40P01"


class _PostgresSerializationFailure(Exception):
    sqlstate = "40001"


@pytest.mark.parametrize(
    ("database_error", "sqlstate"),
    [
        (_PostgresDeadlock("deadlock detected"), "40P01"),
        (_PostgresSerializationFailure("could not serialize access"), "40001"),
    ],
)
def test_postgres_transaction_conflicts_are_retryable(
    database_error: Exception,
    sqlstate: str,
) -> None:
    failure = classify_job_failure(
        DBAPIError("INSERT", {}, database_error, connection_invalidated=False)
    )

    assert failure.code == "job_database_conflict"
    assert failure.retryable is True
    assert failure.details == {"exception_type": "DBAPIError", "sqlstate": sqlstate}
