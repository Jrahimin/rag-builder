"""Complete quantity spans retain their scale through preparation and publication."""

from dataclasses import replace

import pytest

from app.modules.conversations.services.message_execution_runner_service import (
    _calculation_operands,
)
from app.platform.domain.content_hash import content_hash
from tests.unit.modules.conversations.test_operand_period_binding import (
    CURRENT,
    PREVIOUS,
    evaluate,
    finalize_formula,
    node,
    ref,
    reviewed_chunk,
)
from tests.unit.modules.conversations.test_phase2_completion import execute_contract
from tests.unit.modules.conversations.test_post_qa_contract import journey

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("separator", [" ", "\n", "\r\n"])
@pytest.mark.parametrize("number,scale", [("1", "lakh"), ("১", "লাখ"), ("১", "লক্ষ")])
@pytest.mark.parametrize("origin", ["user", "source"])
def test_complete_scaled_quantity_keeps_exact_attested_span(separator, number, scale, origin):
    amount = f"{number}{separator}{scale}"
    text = f"For AY 2026-27 the fee is Tk {amount}."
    registry = _calculation_operands(
        text if origin == "user" else "For AY 2026-27 use the rate 10%.",
        [reviewed_chunk([text], content="Unreviewed preface\n" + text)]
        if origin == "source"
        else [],
    )
    binding = registry["bindings"][ref(registry, "100000", origin)]
    assert binding["quote"] == amount
    assert binding["span_hash"] == content_hash(amount)
    assert binding["period"] == CURRENT and not binding["period_unresolved"]
    assert binding["unit"] == "BDT" and binding["origin"] == origin
    source_text = "Unreviewed preface\n" + text if origin == "source" else text
    assert source_text[binding["char_start"] : binding["char_end"]] == amount
    assert binding["period_quote"] == text
    if origin == "source":
        assert binding["proof_quote_hash"] == content_hash(text)
        assert binding["proof_char_start"] == len("Unreviewed preface\n")


@pytest.mark.parametrize("separator", [" ", "\n", "\r\n"])
@pytest.mark.parametrize("origin", ["user", "source"])
def test_wrapped_amount_does_not_merge_independent_year_rows(separator, origin):
    text = (
        f"For AY 2025-26 the fee is Tk 1{separator}lakh.\n"
        f"For AY 2026-27 the fee is Tk 2{separator}lakh."
    )
    registry = _calculation_operands(
        text + "\nFor AY 2026-27 use the rate 10%."
        if origin == "user"
        else "For AY 2026-27 use the rate 10%.",
        [reviewed_chunk([text])] if origin == "source" else [],
    )
    old, current, rate = (
        ref(registry, "100000", origin),
        ref(registry, "200000", origin),
        ref(registry, "0.1", "user"),
    )
    valid, invalid = evaluate(
        registry,
        [
            node("mixed", "addition", [old, current], "300000"),
            node("current_tax", "multiplication", [current, rate], "20000"),
        ],
    )
    assert valid == {"current_tax": "200000 * 0.1 = 20000.0"}
    assert invalid == {"mixed": "incompatible_period"}
    assert registry["bindings"][old]["period"] == PREVIOUS
    assert registry["bindings"][current]["period"] == CURRENT


@pytest.mark.parametrize("separator", ["\n", "\r\n"])
def test_wrapped_amount_does_not_borrow_period_from_a_different_clause(separator):
    text = f"For AY 2026-27 the rate is 10%.\nThe fee is Tk 1{separator}lakh."
    registry = _calculation_operands(text, [])
    valid, invalid = evaluate(
        registry,
        [
            node(
                "fee_tax",
                "multiplication",
                [ref(registry, "100000", "user"), ref(registry, "0.1", "user")],
                "10000",
            )
        ],
    )
    assert not valid and invalid == {"fee_tax": "unestablished_period"}


@pytest.mark.parametrize("separator", [" ", "\n", "\r\n"])
@pytest.mark.parametrize("scale", ["lakh", "লাখ"])
@pytest.mark.parametrize("origin", ["user", "source"])
@pytest.mark.parametrize("transport", ["regular", "sse"])
async def test_wrapped_quantity_preserves_independent_rules_and_terminal_persistence(
    separator, scale, origin, transport
):
    service, provider, conversation, repository, question = journey()
    prepared_results = []
    original_prepare = service._runner.prepare

    async def prepare(**kwargs):
        prepared = await original_prepare(**kwargs)
        prepared_results.append(prepared)
        return prepared

    service._runner.prepare = prepare
    text = f"The fee is Tk 1{separator}{scale}."
    if origin == "user":
        question += " " + text
    else:
        original = provider.source.content
        provider.source = replace(
            provider.source,
            content=original + "\n" + text,
            chunk_hash=content_hash(original + "\n" + text),
            char_start=None,
            char_end=None,
        )
        service._retrieval.retrieve.return_value = replace(
            service._retrieval.retrieve.return_value, chunks=[provider.source]
        )
        original_review = provider.review

        def review():
            requirements, coverage = original_review()
            coverage["checks"][0]["evidence"].append(
                {
                    "chunk_id": str(provider.source.chunk_id),
                    "start_line": len(original.splitlines()) + 1,
                    "end_line": len(provider.source.content.splitlines()),
                }
            )
            return requirements, coverage

        provider.review = review
    answer = await execute_contract(
        service, provider, conversation, repository, question, transport
    )
    assert answer["terminal_outcome"]["outcome"] == "answered"
    assert answer["grounded"] and len(answer["claims"]) == 2
    assert "18 months" in answer["content"] and "15 months" in answer["content"]
    assert answer["metadata"]["lifecycle"]["persistence_completed"]
    assert "claim_verification" in provider.purposes
    bindings = prepared_results[-1].retrieval_diagnostics["answer_draft"]["calculation_operands"]
    attested = [
        binding
        for binding in bindings.values()
        if binding["origin"] == origin and binding["quote"] == f"1{separator}{scale}"
    ]
    assert len(attested) == 1 and attested[0]["value"] == "100000"


@pytest.mark.parametrize("separator", [" ", "\n", "\r\n"])
@pytest.mark.parametrize("scale", ["lakh", "লাখ"])
@pytest.mark.parametrize("origin", ["user", "source"])
async def test_wrapped_quantity_reaches_real_calculation_finalizer(separator, scale, origin):
    overrides = (
        {
            "question": (
                f"For AY 2026-27 my income is Tk 0.02{separator}{scale}. "
                "Calculate using the reviewed rate."
            )
        }
        if origin == "user"
        else {"quote": f"For AY 2026-27 the rate is 10% and the fee is Tk 1{separator}{scale}."}
    )
    result, diagnostics, verification, _ = await finalize_formula(
        "2000 * 0.1 = 200", retain_rule=origin == "user", **overrides
    )
    assert result.finalization.terminal.outcome == "answered" and result.grounded
    assert len(result.claims) == len(verification) == (2 if origin == "user" else 1)
    expression = "2000.00 * 0.1 = 200.000" if origin == "user" else "2000 * 0.1 = 200.0"
    assert expression in result.content
    assert not diagnostics["calculation_failures"]
    wrapped = [
        binding
        for binding in diagnostics["calculation_operands"].values()
        if binding["origin"] == origin and scale in binding["quote"]
    ]
    assert len(wrapped) == 1
    assert wrapped[0]["quote"] == f"{'0.02' if origin == 'user' else '1'}{separator}{scale}"
    assert wrapped[0]["period"] == CURRENT and wrapped[0]["unit"] == "BDT"


@pytest.mark.parametrize("separator", ["\n", "\r\n"])
async def test_cross_year_wrapped_operand_failure_retains_an_independent_rule(separator):
    result, diagnostics, verification, source = await finalize_formula(
        "2000 * 0.1 = 200",
        question=(
            f"For AY 2026-27 my income is Tk 0.02{separator}lakh. "
            "Calculate using the reviewed rate."
        ),
        quote="For AY 2025-26 the rate is 10%.",
        retain_rule=True,
    )
    assert result.finalization.terminal.outcome == "partial" and result.grounded
    assert diagnostics["calculation_failures"] == {"tax": "incompatible_period"}
    assert len(result.claims) == len(verification) == 1
    assert source.content in result.content and "2000 *" not in result.content
