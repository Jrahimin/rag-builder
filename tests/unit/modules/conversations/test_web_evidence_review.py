import json
from dataclasses import replace
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest

from app.modules.conversations.services.web_evidence_review import (
    review_web_evidence,
    scoped_web_query,
)
from app.platform.providers.contracts.llm import ChatCompletionResult, ChatUsage
from app.platform.providers.contracts.web_search import WebSearchEvidence
from app.platform.providers.errors import ProviderTimeoutError

pytestmark = pytest.mark.unit


def source(text="The United States investment tax credit is 30%."):
    return WebSearchEvidence(
        "source-1",
        "Investment credit",
        "https://example.test/rule",
        text,
        datetime(2026, 9, 8, tzinfo=UTC),
        citation_verified=True,
    )


async def review(verdict, evidence=None, finish="stop"):
    llm = AsyncMock()
    llm.generate.return_value = ChatCompletionResult(
        json.dumps(verdict), "fake", "test", finish, ChatUsage(20, 10), "1"
    )
    result = await review_web_evidence(
        llm=llm,
        query="What is the current investment rebate rate?",
        evidence=evidence or [source()],
        domain_instructions="Bangladesh individual income tax.",
        reference_date=date(2026, 9, 8),
    )
    return result, llm


async def test_foreign_rule_with_shared_tax_words_does_not_reach_generation():
    (accepted, diagnostics, _), llm = await review({"accepted": []})
    assert accepted == []
    assert diagnostics["rejected_scope_count"] == 1
    assert "Bangladesh" in llm.generate.call_args.args[0][0].content
    assert "United States" in llm.generate.call_args.args[0][1].content


@pytest.mark.parametrize("index,quote", [(99, "The rate is 10%."), (0, "The rate is 10%.")])
async def test_unknown_source_or_fabricated_quote_fails_closed(index, quote):
    (accepted, diagnostics, _), _ = await review(
        {"accepted": [{"source_index": index, "quote": quote}]}
    )
    assert not accepted
    assert diagnostics["status"] == "invalid_proof"


async def test_scoped_source_with_exact_proof_is_accepted():
    item = source("Bangladesh investment rebate is ten percent under the current rule.")
    (accepted, diagnostics, usage), _ = await review(
        {"accepted": [{"source_index": 0, "quote": item.content}]}, [item]
    )
    assert accepted == [item]
    assert diagnostics["status"] == "reviewed"
    assert usage == ChatUsage(20, 10)


async def test_invalid_proof_does_not_discard_an_independently_verified_source():
    first = source("Bangladesh investment rebate is ten percent under the current rule.")
    second = source("Bangladesh companies must file an annual return by June 30.")
    second = replace(second, evidence_id="source-2", url="https://example.test/return")
    (accepted, diagnostics, _), _ = await review(
        {
            "accepted": [
                {"source_index": 0, "quote": first.content},
                {"source_index": 1, "quote": "Fabricated quote not found in the second page."},
            ]
        },
        [first, second],
    )
    assert accepted == [first]
    assert diagnostics["status"] == "reviewed_partial"
    assert diagnostics["accepted_indexes"] == [0]
    assert diagnostics["invalid_proof_count"] == 1


async def test_truncated_review_never_admits_web_sources():
    (accepted, diagnostics, _), _ = await review({"accepted": []}, finish="length")
    assert not accepted
    assert diagnostics["status"] == "review_incomplete"


@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (TimeoutError(), "review_timeout"),
        (
            ProviderTimeoutError("review request timed out", provider_name="test"),
            "review_provider_failed",
        ),
    ],
)
async def test_review_failure_status_distinguishes_timeout_from_provider_failure(
    failure, expected_status
):
    llm = AsyncMock()
    llm.generate.side_effect = failure
    accepted, diagnostics, _ = await review_web_evidence(
        llm=llm,
        query="What is the current investment rebate rate?",
        evidence=[source()],
        domain_instructions="Bangladesh tax.",
        reference_date=date(2026, 9, 8),
        timeout_seconds=1,
    )
    assert accepted == []
    assert diagnostics["status"] == expected_status


def test_web_query_retains_project_scope_and_reference_date():
    query = scoped_web_query("current rebate rate", "Bangladesh tax", date(2026, 9, 8))
    assert "Bangladesh tax" in query and "2026-09-08" in query
    assert "Question: explicit question" in scoped_web_query(
        "explicit question", "", date(2026, 9, 8)
    )
    assert "2026-09-08" in scoped_web_query("explicit question", "", date(2026, 9, 8))


def test_web_query_uses_scope_summary_without_full_answer_policy():
    instructions = (
        "Bangladesh tax and business-law sources.\n\n"
        "Detailed salary calculation policy that belongs in answer generation."
    )
    query = scoped_web_query("company AGM", instructions, date(2026, 9, 25))
    assert "Bangladesh tax and business-law sources." in query
    assert "Detailed salary calculation" not in query


def test_web_query_finds_scope_facts_after_style_instructions_and_across_paragraphs():
    instructions = (
        "This project supports compliance research for small businesses.\n\n"
        "Answer in concise bullet points.\n"
        "Jurisdiction: Bangladesh. Assessment year: 2026-27.\n"
        "Prefer official sources for current filing deadlines."
    )
    query = scoped_web_query("company filing deadlines", instructions, date(2026, 9, 25))
    assert "This project supports compliance research for small businesses." in query
    assert "Bangladesh" in query
    assert "2026-27" in query
    assert "official sources" in query
    assert "concise bullet points" not in query


@pytest.mark.parametrize(
    "requirement_id,accepted", [("R2", True), ("R1", False), ("foreign", False)]
)
async def test_web_review_binds_only_missing_requirement_ids(requirement_id, accepted):
    item = source("An official rule applies to the requested period.")
    llm = AsyncMock()
    llm.generate.return_value = ChatCompletionResult(
        json.dumps(
            {
                "accepted": [
                    {"source_index": 0, "quote": item.content, "requirement_ids": [requirement_id]}
                ]
            }
        ),
        "fixture",
        "fixture",
        "stop",
        ChatUsage(1, 1),
        "1",
    )
    result, diagnostics, _ = await review_web_evidence(
        llm=llm,
        query="Which rule?",
        evidence=[item],
        domain_instructions="",
        reference_date=date(2026, 10, 1),
        missing_requirements=[{"requirement_id": "R2", "description": "Missing independent rule"}],
        canonical_scope={"temporal_basis": "known_at", "exact_as_of": "2026-10-01"},
    )
    assert bool(result) is accepted
    payload = json.loads(llm.generate.call_args.args[0][1].content)
    assert payload["canonical_scope"]["temporal_basis"] == "known_at"
    assert diagnostics["requested_requirement_ids"] == ["R2"]


@pytest.mark.parametrize("corruption", [None, "authority", "applicability", "scope"])
async def test_official_web_admission_requires_exact_authority_and_applicability(corruption):
    import hashlib

    scope = {"requested_periods": [{"kind": "assessment", "start_year": 2026, "end_year": 2027}]}
    quote = "Official published rule. Applies to assessment year 2026-27."
    item = source(quote)
    proof = {
        "source_index": 0,
        "quote": quote,
        "requirement_ids": ["R1"],
        "official_authority_quote": "Official published rule.",
        "applicability_quote": "Applies to assessment year 2026-27.",
        "scope_hash": hashlib.sha256(
            json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest(),
    }
    if corruption:
        proof[
            {
                "authority": "official_authority_quote",
                "applicability": "applicability_quote",
                "scope": "scope_hash",
            }[corruption]
        ] = "foreign"
    llm = AsyncMock()
    llm.generate.return_value = ChatCompletionResult(
        json.dumps({"accepted": [proof]}), "fixture", "fixture", "stop", ChatUsage(1, 1), "1"
    )
    accepted, diagnostics, _ = await review_web_evidence(
        llm=llm,
        query="Which rule?",
        evidence=[item],
        domain_instructions="",
        reference_date=date(2026, 10, 1),
        missing_requirements=[{"requirement_id": "R1"}],
        canonical_scope=scope,
        require_official_authority=True,
    )
    assert bool(accepted) is (corruption is None)
    if corruption is None:
        assert diagnostics["proof_bindings"][0]["scope_hash"] == proof["scope_hash"]


@pytest.mark.parametrize(
    "corruption", [None, "affected", "missing_scope", "discovery", "dependency"]
)
def test_independent_web_requirement_cannot_escape_unresolved_dependency(corruption):
    from dataclasses import replace

    from app.modules.conversations.services.web_evidence_review import independent_web_requirements
    from tests.unit.modules.conversations.test_phase2_completion import source

    chunk = source("Independent operative rule.")
    chunk = replace(
        chunk,
        metadata={
            "source_revision_id": "clear-revision",
            "source_policy_applicable": corruption != "missing_scope",
            "index_build_id": "build",
            "source_metadata_generation": 2,
        },
    )
    requirement = {
        "requirement_id": "R",
        "depends_on": ["missing"] if corruption == "dependency" else [],
    }
    checks = [
        {
            "requirement_id": "R",
            "supported": True,
            "fulfillment": "partial",
            "evidence": [{"chunk_id": str(chunk.chunk_id), "quote": chunk.content}],
        }
    ]
    rows = independent_web_requirements(
        [requirement],
        checks,
        [chunk],
        {"clear-revision"} if corruption == "affected" else {"unresolved-revision"},
        discovery_complete=corruption != "discovery",
    )
    assert bool(rows) is (corruption is None)
