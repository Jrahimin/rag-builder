"""Arithmetic diagnostics must not certify source rules or unchecked expressions."""

import pytest

from app.modules.conversations.grounding_service import _arithmetic_consistency, _claim_kind


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1,200,000 - 400,000 = 800,000", "supported"),
        ("The result is BDT 1,200,000 \u2212 BDT 400,000 = BDT 800,000.", "supported"),
        ("45,000 \u2212 6,000 = 39,000", "supported"),
        ("45,000 \u2212 6,000 = 40,000", "unsupported"),
        ("60,000 \u00d7 10% = 6,000", "supported"),
        ("60,000 \u00d7 10% = 6,000; 45,000 - 6,000 = 40,000", "unsupported"),
        ("10 + 20 + 30 = 50", "unverified"),
        ("min(10, 20) = 10", "unverified"),
        ("Tax rate is 10%", None),
    ],
)
def test_arithmetic_diagnostics(expression, expected):
    assert _arithmetic_consistency(expression) == expected


def test_claim_categories_preserve_difference_between_inputs_assumptions_and_rules():
    assert (
        _claim_kind("| Gross salary supplied | 1,200,000 |", "salary BDT 1200000")
        == "scenario_input"
    )
    assert _claim_kind("Salary BDT 1200000", "Salary BDT 1200000; age 35") == "scenario_input"
    assert _claim_kind("Assuming gross salary", "") == "assumption"
    assert _claim_kind("Please provide TDS to refine this", "") == "refinement"
    assert _claim_kind("Interest is exempt", "Salary BDT 1200000") == "source_assertion"
