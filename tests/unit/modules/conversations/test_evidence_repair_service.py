"""Recovery must respect snapshot, scope, authority, and final context budgets."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.core.config import ChatConfig, RetrievalConfig
from app.modules.conversations.grounding_service import GroundingService
from app.modules.conversations.ports import ContextChunk, ContextRetrievalResult
from app.modules.conversations.services.evidence_repair_service import repair_knowledge_evidence
from app.modules.conversations.turn_resolution import EffectiveRetrievalInputs
from app.platform.domain.content_hash import content_hash
from app.platform.providers.contracts.llm import ChatCompletionResult, ChatUsage

pytestmark = pytest.mark.unit


def chunk(text: str, **metadata: object) -> ContextChunk:
    return ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=1,
        content=text,
        score=0.95,
        semantic_score=0.95,
        filename="current guide.pdf",
        chunk_hash=content_hash(text),
        metadata=metadata,
    )


async def run_repair(branches, *, queries=None, config=None, initial_records=None):
    config = config or ChatConfig()
    queries = (
        queries
        if queries is not None
        else ["gross salary exemption", "general rate bands", "current investment rebate"]
    )
    llm = AsyncMock()
    llm.generate.return_value = ChatCompletionResult(
        content=json.dumps({"queries": queries}),
        provider="fake",
        model="test",
        finish_reason="stop",
        usage=ChatUsage(12, 8),
        provider_version="1",
    )
    retrieval = AsyncMock()
    snapshot = {"index_build_id": "build-a", "source_metadata_generation": 24}
    retrieval.retrieve.side_effect = [
        ContextRetrievalResult(chunks=items, diagnostics={**snapshot, **diagnostics})
        for items, diagnostics in branches
    ]
    inputs = EffectiveRetrievalInputs(
        query="Calculate from gross salary and eligible investment for this period.",
        document_id=uuid.uuid4(),
        metadata_filter={"region": "local"},
        as_of=datetime(2026, 7, 1, tzinfo=UTC),
        suppress_web=True,
    )
    result = await repair_knowledge_evidence(
        inputs=inputs,
        initial=ContextRetrievalResult(
            chunks=[],
            diagnostics={
                **snapshot,
                "modifies_expansion_records": initial_records or [],
            },
        ),
        selected=[],
        retrieval=retrieval,
        llm=llm,
        grounding=GroundingService(config),
        chat_config=config,
        retrieval_config=RetrievalConfig(),
        max_output_tokens=1024,
    )
    return result, retrieval, inputs


async def test_recovers_separate_salary_band_and_rebate_dependencies_with_original_scope():
    texts = [
        "Employment income exemption: one third of gross salary, capped at 500000.",
        "General rate bands for 2026: first 400000 nil, next 300000 ten percent.",
        "Current investment rebate is the lower of three percent of income, "
        "ten percent of investment and 750000.",
    ]
    result, retrieval, inputs = await run_repair([([chunk(text)], {}) for text in texts])
    assert result.diagnostics["status"] == "recovered"
    assert [item.content for item in result.selected] == texts
    assert result.decision is not None and result.decision.sufficient
    assert result.usage == ChatUsage(12, 8)
    for call in retrieval.retrieve.call_args_list:
        assert call.kwargs["document_id"] == inputs.document_id
        assert call.kwargs["metadata_filter"] == inputs.metadata_filter
        assert call.kwargs["as_of"] == inputs.as_of


@pytest.mark.parametrize(
    "change", [{"index_build_id": "build-b"}, {"source_metadata_generation": 25}]
)
async def test_does_not_mix_snapshots_during_repair(change):
    result, _, _ = await run_repair([([chunk("Gross salary exemption")], change)])
    assert result.diagnostics["status"] == "snapshot_changed"
    assert result.decision is None and not result.selected


async def test_old_rule_cannot_become_trusted_when_a_branch_omits_its_relationship():
    old = chunk("Current investment rebate is fifteen percent.", source_revision_id="old")
    result, _, _ = await run_repair(
        [([old], {})],
        queries=["current investment rebate"],
        initial_records=[
            {
                "base_revision_id": "old",
                "modifier_revision_id": "new",
                "outcome": "ungoverned_or_incomplete_metadata",
                "target_provisions": [],
            }
        ],
    )
    assert result.diagnostics["status"] == "dependency_unresolved"
    assert not result.selected


async def test_orphan_table_never_repairs_a_dependency_despite_high_similarity():
    table = chunk("Rate bands: first 450000 nil", element_type="table")
    result, _, _ = await run_repair([([table], {})], queries=["general rate bands"])
    assert result.diagnostics["status"] == "dependency_unresolved"


async def test_later_branch_cannot_leave_an_earlier_unknown_rule_in_final_context():
    earlier = chunk("General rate bands", source_revision_id="old")
    result, _, _ = await run_repair(
        [
            ([earlier], {}),
            (
                [chunk("Current investment rebate")],
                {
                    "modifies_expansion_records": [
                        {
                            "base_revision_id": "old",
                            "modifier_revision_id": "new",
                            "outcome": "ungoverned_or_incomplete_metadata",
                            "target_provisions": [],
                        }
                    ]
                },
            ),
        ],
        queries=["general rate bands", "current investment rebate"],
    )
    assert result.diagnostics["status"] == "dependency_unresolved"
    assert result.decision is None and not result.selected


async def test_final_budget_must_retain_every_dependency():
    result, _, _ = await run_repair(
        [
            ([chunk(text)], {})
            for text in [
                "gross salary exemption",
                "general rate bands",
                "current investment rebate",
            ]
        ],
        config=ChatConfig(max_context_chunks=2),
    )
    assert result.diagnostics["status"] == "dependency_exceeds_budget"
    assert not result.selected


@pytest.mark.parametrize("queries", [[], [" "], ["q"] * 4, ["q" * 501]])
async def test_invalid_plans_never_search_or_authorize_generation(queries):
    result, retrieval, _ = await run_repair([], queries=queries)
    assert result.decision is None
    retrieval.retrieve.assert_not_called()


async def test_non_tax_policy_recovery_uses_the_same_path():
    result, _, _ = await run_repair(
        [([chunk("Enterprise renewal charges are waived for eligible customers.")], {})],
        queries=["Enterprise renewal eligibility and charges"],
    )
    assert result.diagnostics["status"] == "recovered"
