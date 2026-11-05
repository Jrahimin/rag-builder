"""Operand-local period proof through the real registry and arithmetic consumer."""

import json
import uuid
from dataclasses import replace
from decimal import Decimal

import pytest

from app.modules.conversations.calculation_graph import CalculationGraph
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.schemas.message import MessageSendRequest
from app.modules.conversations.services.message_execution_runner_service import (
    _calculation_operands,
)
from app.modules.conversations.turn_resolution import normalize_request_scope
from app.platform.domain.content_hash import content_hash
from app.platform.providers.contracts.llm import ChatCompletionResult, ChatUsage
from app.platform.providers.request_work import ObservedLLM
from tests.unit.modules.conversations.test_post_qa_contract import journey

pytestmark = pytest.mark.unit

CURRENT = "assessment:2026-2027"
PREVIOUS = "assessment:2025-2026"


def reviewed_chunk(quotes, *, content=None, metadata=None):
    text = content if content is not None else "\n".join(quotes)
    return ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=text,
        score=1.0,
        filename="period-proof.txt",
        chunk_hash=content_hash(text),
        metadata={
            **(metadata or {}),
            "reviewed_proof": [{"requirement_id": "R", "quote": q} for q in quotes],
        },
    )


def ref(registry, value, origin):
    matches = [
        key
        for key, binding in registry["bindings"].items()
        if Decimal(binding["value"]) == Decimal(value) and binding["origin"] == origin
    ]
    assert len(matches) == 1
    return matches[0]


def evaluate(registry, nodes):
    return CalculationGraph.model_validate({"nodes": nodes}).component_expressions(
        registry["inputs"], registry["sources"], registry["bindings"]
    )


def node(identity, operation, operands, result):
    return {"id": identity, "operation": operation, "operands": operands, "result": result}


@pytest.mark.parametrize("digits", ["ascii", "bangla"])
@pytest.mark.parametrize("separator", [". ", "; ", "\n", "। "])
def test_current_user_operand_survives_an_unrelated_user_year(digits, separator):
    question = separator.join(
        [
            "For AY 2025-26 my income was Tk 1000",
            "For AY 2026-27 my income is Tk 2000",
            "Calculate only AY 2026-27.",
        ]
    )
    quote = "For AY 2026-27 the rate is 10%."
    if digits == "bangla":
        folding = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")
        question, quote = question.translate(folding), quote.translate(folding)
    registry = _calculation_operands(question, [reviewed_chunk([quote])])
    current, previous, rate = (
        ref(registry, "2000", "user"),
        ref(registry, "1000", "user"),
        ref(registry, "0.1", "source"),
    )
    valid, invalid = evaluate(
        registry,
        [
            node("cross_year", "addition", [previous, current], "3000"),
            node("dependent", "addition", ["node:cross_year", current], "5000"),
            node("current_tax", "multiplication", [current, rate], "200"),
        ],
    )
    assert valid == {"current_tax": "2000 * 0.1 = 200.0"}
    assert invalid == {"cross_year": "incompatible_period", "dependent": "dependency_failed"}
    assert registry["bindings"][current]["period"] == CURRENT
    assert registry["bindings"][previous]["period"] == PREVIOUS
    assert registry["bindings"]["node:current_tax"]["period"] == CURRENT


@pytest.mark.parametrize("digits", ["ascii", "bangla"])
@pytest.mark.parametrize("layout", ["separate_quotes", "sentences", "clauses", "rows"])
def test_source_periods_remain_bound_to_each_reviewed_clause_or_row(digits, layout):
    old = "For AY 2025-26 the fee is Tk 1000"
    current = "For AY 2026-27 the fee is Tk 2000"
    separator = {"sentences": ". ", "clauses": "; ", "rows": "\n"}.get(layout, "\n")
    if layout == "rows":
        old, current = "AY 2025-26 | Tk 1000", "AY 2026-27 | Tk 2000"
    quotes = [old, current] if layout == "separate_quotes" else [separator.join([old, current])]
    question = "For AY 2026-27 use the rate 10%."
    if digits == "bangla":
        folding = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")
        quotes, question = [q.translate(folding) for q in quotes], question.translate(folding)
    chunk = reviewed_chunk(quotes)
    registry = _calculation_operands(question, [chunk])
    previous, current, rate = (
        ref(registry, "1000", "source"),
        ref(registry, "2000", "source"),
        ref(registry, "0.1", "user"),
    )
    valid, invalid = evaluate(
        registry,
        [
            node("mixed", "addition", [previous, current], "3000"),
            node("current_tax", "multiplication", [current, rate], "200"),
        ],
    )
    assert valid == {"current_tax": "2000 * 0.1 = 200.0"}
    assert invalid == {"mixed": "incompatible_period"}
    for key, period in [(previous, PREVIOUS), (current, CURRENT)]:
        binding = registry["bindings"][key]
        assert binding["period"] == period
        assert binding["origin"] == "source" and binding["source_hash"] == chunk.chunk_hash
        assert binding["quote"] == chunk.content[binding["char_start"] : binding["char_end"]]
        assert binding["span_hash"] == content_hash(binding["quote"])
        proof_quote = chunk.content[binding["proof_char_start"] : binding["proof_char_end"]]
        assert proof_quote in quotes
        assert binding["proof_quote_hash"] == content_hash(proof_quote)


@pytest.mark.parametrize("origin", ["user", "source"])
@pytest.mark.parametrize(
    "ambiguous",
    [
        "For AY 2025-26 and AY 2026-27 the fee is Tk 1000.",
        "For AY 2025-26 and 2026-27 the fee is Tk 1000.",
        "For AY 2025-26 and AY 2026-27. The fee is Tk 1000.",
        "For AY 2025-26 the rate is 20%. The fee is Tk 1000.",
    ],
)
def test_ambiguous_period_is_local_failure_and_never_metadata_or_union(origin, ambiguous):
    independent = "For AY 2026-27 the fee is Tk 2000 and the rate is 10%."
    registry = _calculation_operands(
        ambiguous if origin == "user" else independent,
        [
            reviewed_chunk(
                [independent if origin == "user" else ambiguous],
                metadata={"applicable_period": CURRENT},
            )
        ],
    )
    uncertain = ref(registry, "1000", origin)
    good_origin = "source" if origin == "user" else "user"
    current, rate = ref(registry, "2000", good_origin), ref(registry, "0.1", good_origin)
    valid, invalid = evaluate(
        registry,
        [
            node("uncertain", "multiplication", [uncertain, rate], "100"),
            node("current_tax", "multiplication", [current, rate], "200"),
        ],
    )
    assert valid == {"current_tax": "2000 * 0.1 = 200.0"}
    assert invalid == {"uncertain": "unestablished_period"}
    assert registry["bindings"][uncertain]["period"] is None


def test_periodless_reviewed_quote_does_not_borrow_another_quotes_year():
    chunk = reviewed_chunk(["For AY 2025-26 the rate is 20%.", "The fee is Tk 1000."])
    registry = _calculation_operands("For AY 2025-26 use the rate 10%.", [chunk])
    valid, invalid = evaluate(
        registry,
        [node("tax", "multiplication", [ref(registry, "1000", "source"), "input:q2"], "100")],
    )
    assert not valid and invalid == {"tax": "unestablished_period"}


@pytest.mark.parametrize("digits", ["ascii", "bangla"])
def test_unlabelled_table_year_kind_is_unresolved_rather_than_shared_union(digits):
    table = "Year | Fee\n2025-26 | Tk 1000\n2026-27 | Tk 2000"
    if digits == "bangla":
        table = table.translate(str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯"))
    registry = _calculation_operands(
        "For AY 2026-27 income Tk 3000 and rate 10%.", [reviewed_chunk([table])]
    )
    valid, invalid = evaluate(
        registry,
        [
            node(
                "table_sum",
                "addition",
                [ref(registry, "1000", "source"), ref(registry, "2000", "source")],
                "3000",
            ),
            node(
                "user_tax",
                "multiplication",
                [ref(registry, "3000", "user"), ref(registry, "0.1", "user")],
                "300",
            ),
        ],
    )
    assert valid == {"user_tax": "3000 * 0.1 = 300.0"}
    assert invalid == {"table_sum": "unestablished_period"}


@pytest.mark.parametrize("text", ["10 days and 2 months", "USD 10 and BDT 2", "income 10"])
def test_operand_period_repair_preserves_unit_and_currency_rejection(text):
    registry = _calculation_operands("For AY 2026-27 " + text, [])
    refs = list(registry["bindings"])
    valid, invalid = evaluate(registry, [node("bad", "addition", refs, "12")])
    assert not valid
    assert invalid == {
        "bad": "unestablished_units" if text == "income 10" else "incompatible_units"
    }


@pytest.mark.parametrize("quote", ["Absent fee Tk 1000", "The fee is Tk 1000."])
def test_unattested_or_repeated_reviewed_quotes_cannot_supply_foreign_operands(quote):
    content = "The fee is Tk 1000.\nThe fee is Tk 1000."
    registry = _calculation_operands(
        "For AY 2026-27 income Tk 2000.", [reviewed_chunk([quote], content=content)]
    )
    assert not registry["sources"]
    with pytest.raises(ValueError, match="foreign"):
        evaluate(
            registry,
            [
                node(
                    "foreign",
                    "multiplication",
                    [ref(registry, "2000", "user"), "source:foreign:q2"],
                    "200",
                )
            ],
        )


def test_decimal_offsets_and_unscoped_arithmetic_are_retained():
    registry = _calculation_operands("The fee is Tk 1000.50 and the rate is 10%.", [])
    valid, invalid = evaluate(
        registry,
        [node("tax", "multiplication", ["input:q0", "input:q1"], "100.05")],
    )
    assert valid == {"tax": "1000.50 * 0.1 = 100.050"} and not invalid
    assert registry["bindings"]["input:q0"]["quote"] == "1000.50"


async def finalize_formula(
    display,
    *,
    question="For AY 2026-27 my income is Tk 2000. Calculate using the reviewed rate.",
    quote="For AY 2026-27 the rate is 10%.",
    foreign=False,
    verdict="supported",
    retain_rule=False,
):
    """Run the publication boundary with an admitted, exact reviewed proof fixture."""
    service, provider, conversation, _, base_question = journey()
    runner = service._runner
    with runner._work.attached():
        prepared = await runner.prepare(
            request=MessageSendRequest(content=base_question),
            current_message_id=uuid.uuid4(),
            generation_history=[],
            resolver_source=[],
            citation_chunks={},
            assistant_metadata_by_id={},
            previous_assistant_metadata={},
            llm=ObservedLLM(provider, runner._work),
            temperature=None,
        )
        assert prepared.evidence.sufficient
        source = replace(
            provider.source,
            content=quote,
            chunk_hash=content_hash(quote),
            char_start=None,
            char_end=None,
            metadata={
                "project_id": str(conversation.project_id),
                "reviewed_proof": [{"requirement_id": "R1", "quote": quote, "fulfillment": "full"}],
            },
        )
        diagnostics = {
            "normalized_scope": normalize_request_scope(question).model_dump(mode="json"),
            "knowledge_repair": {
                "status": "initial_evidence_complete",
                "requirements": [
                    {
                        "requirement_id": "R1",
                        "description": "Calculate the current-year amount using the reviewed rate",
                        "origin": "explicit_user_request",
                        "materiality": "central_rule",
                    }
                ],
                "coverage": {
                    "complete": True,
                    "quotes_validated": True,
                    "checks": [
                        {
                            "requirement_id": "R1",
                            "supported": True,
                            "fulfillment": "full",
                            "evidence": [{"chunk_id": str(source.chunk_id), "quote": quote}],
                        }
                    ],
                },
            },
        }
        prepared = replace(
            prepared,
            selected=[source],
            knowledge_selected=[source],
            chunks=[source],
            retrieval_diagnostics=diagnostics,
            response_policy={
                "answerable_scope": {
                    "complete": True,
                    "partial": False,
                    "supported_requirement_ids": ["R1"],
                }
            },
        )
        runner._active_prepared = prepared
        registry = _calculation_operands(question, [source])
        income, rate = ref(registry, "2000", "user"), ref(registry, "0.1", "source")
        segments = [
            {
                "text": display,
                "requirement_ids": ["R1"],
                "proof_ids": [str(source.chunk_id)],
                "calculation_references": ["tax"],
            }
        ]
        if retain_rule:
            segments.append(
                {"text": quote, "requirement_ids": ["R1"], "proof_ids": [str(source.chunk_id)]}
            )
        draft = {
            "version": "answer.draft.v1",
            "segments": segments,
            "calculations": {
                "nodes": [
                    node(
                        "tax",
                        "multiplication",
                        [income, "source:foreign:q0" if foreign else rate],
                        "200",
                    )
                ]
            },
        }
        verification = []

        async def generate(messages, **kwargs):
            items = json.loads(messages[1].content)
            verification.extend(items)
            return ChatCompletionResult(
                content=json.dumps(
                    {
                        "version": "claim.entailment.v2",
                        "verdicts": [
                            {
                                "assertion_id": item["assertion_id"],
                                "proof_hashes": item["proof_hashes"],
                                "scope_hash": item["scope_hash"],
                                "failed_dimensions": [] if verdict == "supported" else ["scope"],
                                "evidence_binding": "controlled exact current-year proof",
                                "status": verdict,
                                "publication_complete": True,
                            }
                            for item in items
                        ],
                    }
                ),
                provider="fixture",
                model="fixture",
                provider_version="fixture",
                finish_reason="stop",
                usage=ChatUsage(1, 1),
            )

        provider.generate = generate
        result = await runner.finalize(
            prepared=prepared,
            content=json.dumps(draft),
            finish_reason="stop",
            input_tokens=1,
            output_tokens=1,
            provider="fixture",
            model="fixture",
            generation_ms=1,
            total_ms=1,
            user_content_for_title=question,
            streamed=False,
            input_tokens_logged=1,
            output_tokens_logged=1,
            generation_ran=True,
        )
        return result, prepared.retrieval_diagnostics["answer_draft"], verification, source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "display",
    [
        "2000 * 0.1 = 200.0",  # Valid graph-rendered control.
        "2000 * 0.1 = 200",
        "2000.00 \u00d7 0.10 = 200.000",
        "2000 * 0.1 = 200".translate(str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")),
        "2000.00000000000000000000000000000 * 0.1 = 200",
    ],
)
async def test_exact_decimal_display_equivalence_reaches_final_scope_verification(display):
    result, diagnostics, verification, source = await finalize_formula(display)
    assert result.finalization.terminal.outcome == "answered" and result.grounded
    assert len(verification) == len(result.claims) == 1
    assert "2000 * 0.1 = 200.0" in result.content
    assert verification[0]["scope_hash"]
    assert verification[0]["proof_hashes"]
    assert not diagnostics["calculation_failures"]
    bindings = diagnostics["calculation_operands"]
    assert {b["period"] for b in bindings.values()} == {CURRENT}
    assert {b["unit"] for b in bindings.values()} == {"BDT", "ratio"}
    source_binding = next(b for b in bindings.values() if b["origin"] == "source")
    assert source_binding["source_hash"] == source.chunk_hash
    assert source_binding["proof_quote_hash"] == content_hash(source.content)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "display",
    [
        "2000 * 0.1 = 201",
        "2001 * 0.1 = 200",
        "2000 + 0.1 = 200",
        "0.1 * 2000 = 200",
        "2 000 * 0.1 = 200",
        "2000 * 0.1 = 2 00",
        "2000 * 0.1 = 200 BDT",
        "2e3 * 0.1 = 200",
        "2000 * 0.1 = NaN",
        "2000 * 0.1 = Infinity",
        "2000 * 0.1 = 200.00000000000000000000000000001",
        "2000.00000000000000000000000000001 * 0.1 = 200",
    ],
)
async def test_display_normalization_rejects_changed_values_tokens_and_syntax(display):
    result, diagnostics, verification, _ = await finalize_formula(display)
    assert result.finalization.terminal.outcome == "verification_failed"
    assert not result.grounded and not result.claims and not verification
    assert diagnostics["rejected_segment_count"] == 1
    assert not diagnostics["calculation_failures"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides, failure",
    [
        ({"quote": "For AY 2025-26 the rate is 10%."}, "incompatible_period"),
        (
            {"question": "For AY 2026-27 income 2000. Calculate using the reviewed rate."},
            "unestablished_units",
        ),
        ({"foreign": True}, None),
    ],
)
async def test_equivalent_formula_cannot_override_operand_proof_failures(overrides, failure):
    result, diagnostics, verification, _ = await finalize_formula("2000 * 0.1 = 200", **overrides)
    assert result.finalization.terminal.outcome == "verification_failed"
    assert not result.claims and not verification
    assert diagnostics["reason"] == "calculation_graph_invalid"
    if failure:
        assert diagnostics["calculation_failures"] == {"tax": failure}


@pytest.mark.asyncio
async def test_equivalent_formula_still_requires_supported_final_scope_verdict():
    result, _, verification, _ = await finalize_formula("2000 * 0.1 = 200", verdict="unsupported")
    assert len(verification) == 1
    assert not result.grounded and not result.claims


@pytest.mark.asyncio
async def test_changed_formula_retains_independently_verified_rule():
    result, diagnostics, verification, source = await finalize_formula(
        "2000 * 0.1 = 201", retain_rule=True
    )
    assert result.finalization.terminal.outcome == "partial" and result.grounded
    assert source.content in result.content and "201" not in result.content
    assert len(verification) == len(result.claims) == 1
    assert diagnostics["rejected_segment_count"] == 1
