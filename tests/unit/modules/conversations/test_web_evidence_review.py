import json
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest

from app.modules.conversations.services.web_evidence_review import (
    review_web_evidence,
    scoped_web_query,
)
from app.platform.providers.contracts.llm import ChatCompletionResult, ChatUsage
from app.platform.providers.contracts.web_search import WebSearchEvidence

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


async def test_truncated_review_never_admits_web_sources():
    (accepted, diagnostics, _), _ = await review({"accepted": []}, finish="length")
    assert not accepted
    assert diagnostics["status"] == "review_incomplete"


def test_web_query_retains_project_scope_and_reference_date():
    query = scoped_web_query("current rebate rate", "Bangladesh tax", date(2026, 9, 8))
    assert "Bangladesh tax" in query and "2026-09-08" in query
    assert scoped_web_query("explicit question", "", date(2026, 9, 8)) == "explicit question"
