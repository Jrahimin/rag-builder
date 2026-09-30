from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.config import ChatConfig
from app.modules.conversations.grounding_service import (
    GroundingService,
    _evidence_money_amounts,
    _money_amounts,
    _parse_calculation,
)
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.quantities import normalize_quantities


@pytest.mark.parametrize(
    "text",
    [
        "For income year 2026, total income is Tk 750,000.",
        "For FY 2026-27, total income is **Tk 7.5 lakh**.",
        "করবর্ষ \u09e8\u09e6\u09e8\u09ec-\u09e8\u09e6\u09e8\u09ed: "
        "মোট আয় \u09ed,\u09eb\u09e6,\u09e6\u09e6\u09e6 টাকা।",
    ],
)
def test_claim_and_evidence_money_agree_without_period_leakage(text):
    assert _money_amounts(text) == {750000}
    assert _evidence_money_amounts(text) == {750000}


@pytest.mark.parametrize("text", ["Tk 2026", "\u09e8\u09e6\u09e8\u09ec টাকা", "**Tk 2026**"])
def test_explicit_year_shaped_currency_remains_money(text):
    assert _money_amounts(text) == _evidence_money_amounts(text) == {2026}


def test_typed_quantities_preserve_spans_and_decimal_scale():
    text = "Section 120: 2.35 lakh income, 15%, 120 days and 500 persons in FY 2026-27."
    quantities = normalize_quantities(text)
    assert [item.kind for item in quantities] == [
        "locator",
        "money",
        "rate",
        "duration",
        "count",
        "period",
        "period",
    ]
    assert quantities[1].value == Decimal("235000.00")
    assert all(text[item.start : item.end] == item.raw for item in quantities)


def test_bare_year_shaped_amount_is_not_excluded_by_value_alone():
    assert _evidence_money_amounts("The fee is 2026.") == {2026}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Tk 1,234.56", Decimal("1234.56")),
        ("BDT 1,23,456.78", Decimal("123456.78")),
        ("৳ ১,২৩,৪৫৬.৭৮", Decimal("123456.78")),
    ],
)
def test_grouped_decimal_money_normalizes_western_indic_and_bengali_digits(text, expected):
    money = [item for item in normalize_quantities(text) if item.kind == "money"]
    assert [item.value for item in money] == [expected]
    assert text[money[0].start : money[0].end] == money[0].raw


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        (
            "Tk 1,234.56 x 10% = Tk 123.46",
            (Decimal("1234.56"), Decimal("10"), Decimal("123.46")),
        ),
        (
            "10% x BDT 1,23,456.78 = BDT 12,345.68",
            (Decimal("123456.78"), Decimal("10"), Decimal("12345.68")),
        ),
        (
            "১০% x ৳ ১,২৩,৪৫৬.৭৮ = ৳ ১২,৩৪৫.৬৮",
            (Decimal("123456.78"), Decimal("10"), Decimal("12345.68")),
        ),
    ],
)
def test_decimal_calculation_parser_accepts_grouped_digit_scripts(expression, expected):
    assert _parse_calculation(expression) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Tk 1,234.56 x 10% = Tk 23.46. [1]", "unsupported"),
        ("Tk 1,234.56 x 10% = Tk 123.46. [1]", "supported"),
    ],
)
async def test_decimal_calculation_claim_checks_grouped_operand_and_result(answer, expected):
    source = ContextChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        chunk_index=0,
        content="The rate is 10%.",
        score=0.9,
        filename="rules.md",
        chunk_hash="rate-source",
    )
    result = await GroundingService(ChatConfig()).map_claims(
        answer, [source], user_input="Supplied amount Tk 1,234.56"
    )
    assert result.claims[0]["verification"] == expected
