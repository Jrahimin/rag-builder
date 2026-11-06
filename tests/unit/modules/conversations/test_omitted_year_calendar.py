"""An explicit Project calendar binds omitted tax years without guessing from hits."""

from datetime import UTC, datetime

import pytest

from app.core.calendar import ProjectCalendarPolicy
from app.modules.conversations.turn_resolution import normalize_request_scope

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What is the tax-free income limit for a general individual taxpayer?", [(2026, 2027)]),
        ("What is the tax-free limit in AY 2025-26?", [(2025, 2026)]),
        ("When must the company hold its AGM?", []),
    ],
)
def test_configured_omitted_assessment_year(question, expected):
    policy = ProjectCalendarPolicy(
        timezone="Asia/Dhaka",
        period_kind="assessment",
        boundary_month=7,
        boundary_day=1,
        default_for_omitted=True,
    )
    scope = normalize_request_scope(
        question, reference_time=datetime(2026, 11, 6, tzinfo=UTC), calendar_policy=policy
    )
    assert [(p.start_year, p.end_year) for p in scope.requested_periods] == expected
