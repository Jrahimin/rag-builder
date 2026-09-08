"""Evaluation summaries must expose wrong amounts without overstating token matches."""

import pytest
from scripts.summarize_rag_enhancements import expected_fact_presence

pytestmark = pytest.mark.unit


def test_tax_amount_checks_accept_bangla_digits_and_lakh_grouping():
    amount = "১,১০,০০০ - ৬,০০০ = ১,০৪,০০০"  # noqa: RUF001 -- Intentional Bengali numerals.
    assert all(expected_fact_presence("taxable", amount).values())


def test_answered_but_wrong_band_allocation_does_not_pass_amount_smoke_check():
    checks = expected_fact_presence("taxable", "105,000 minus 6,000 equals 99,000")
    assert checks == {"110000": False, "6000": True, "104000": False}


def test_rebate_percentage_checks_do_not_accept_fifteen_for_ten():
    assert not expected_fact_presence("rebate", "3%, 15%, cap 750,000")["10%"]


@pytest.mark.parametrize("unit", ["lakh", "লাখ"])
def test_rebate_cap_accepts_equivalent_lakh_notation(unit):
    assert all(expected_fact_presence("rebate", f"3%, 10%, cap 7.50 {unit}").values())
