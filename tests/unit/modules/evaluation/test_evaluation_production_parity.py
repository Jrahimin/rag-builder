"""Focused checks that evaluation uses the production answer contract."""

from __future__ import annotations

import uuid
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest

from app.composition.evaluation import GroundedEvaluationAnswerAdapter
from app.core.config import Settings
from app.modules.conversations.grounding_service import GroundingResult
from app.modules.evaluation.ports import QualityHit
from app.platform.providers.implementations.echo_chat import EchoLLMProvider

pytestmark = pytest.mark.unit


async def test_evaluation_claim_mapping_requires_the_same_citations_as_chat(monkeypatch) -> None:
    adapter = GroundedEvaluationAnswerAdapter(
        settings=Settings(),
        llm=EchoLLMProvider(model="test", provider_version="1"),
    )
    mapping = AsyncMock(
        return_value=GroundingResult(claims=[], grounded=False, citation_coverage=0.0)
    )
    from app.modules.conversations.grounding_service import GroundingService

    monkeypatch.setattr(GroundingService, "map_claims", mapping)
    hit = QualityHit(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        content="The refund period is within 30 days.",
        score=0.9,
        semantic_score=0.9,
        filename="policy.txt",
        chunk_index=0,
    )

    answer = await adapter.answer(
        profile="semantic",
        question="What is the refund period?",
        hits=[hit],
    )

    assert answer.generation_ran is True
    assert mapping.await_count >= 1
    assert mapping.await_args.kwargs.get("require_citations", True) is True
    assert "user_input" in mapping.await_args.kwargs
    assert answer.execution["terminal"]["outcome"] in {
        "insufficient_evidence",
        "verification_failed",
    }


async def test_complete_execution_owns_first_scoped_search_clock_and_consecutive_cases(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from app.modules.conversations import turn_resolution
    from app.modules.evaluation.ports import EvaluationCaseInput
    from app.modules.retrieval.schemas.search import SearchDiagnostics

    monkeypatch.setattr(
        turn_resolution, "utc_reference_datetime", lambda: datetime(2026, 10, 1, tzinfo=UTC)
    )
    calls = []
    clock = [100.0]
    monkeypatch.setattr("app.platform.providers.request_work.time.perf_counter", lambda: clock[0])
    settings = Settings()

    class ScopedSearch:
        _config = settings.retrieval
        resolved_query_embedder = None

        def set_request_scope(self, scope):
            self.scope = scope

        async def search(self, request, **kwargs):
            clock[0] += 0.015
            await asyncio.sleep(0)
            calls.append((request, dict(self.scope)))
            return SimpleNamespace(
                results=[],
                diagnostics=SearchDiagnostics(
                    normalized_scope=self.scope,
                    strategy=settings.retrieval.strategy,
                    duration_ms=15,
                    rerank_requested=False,
                    rerank_status="not_run",
                ),
            )

    class Retrieval:
        _services: ClassVar[dict[str, SimpleNamespace]] = {
            "semantic": SimpleNamespace(_config=settings.retrieval)
        }

        @asynccontextmanager
        async def case_search(self, profile):
            yield ScopedSearch(), SimpleNamespace(rollback=AsyncMock())

    adapter = GroundedEvaluationAnswerAdapter(
        settings=settings,
        llm=EchoLLMProvider(model="test", provider_version="1"),
        retrieval=Retrieval(),
    )
    document = uuid.uuid4()
    for question, expected_year in [
        ("What does the archive say in 2024-25?", 2024),
        ("What does the archive say now?", None),
    ]:
        execution = await adapter.execute_case(
            profile="semantic",
            case=EvaluationCaseInput(
                query=question,
                top_k=7,
                document_id=document,
                metadata_filter={"category": "archive"},
            ),
        )
        request, scope = calls[-1]
        assert request.document_id == document and request.metadata_filter == {
            "category": "archive"
        }
        assert request.top_k == 7
        assert scope["category"] == "archive"
        assert [p["start_year"] for p in scope["requested_periods"]] == (
            [expected_year] if expected_year else []
        )
        assert execution.answer.execution["normalized_scope"] == scope
        assert execution.search.latency_ms == 15
        assert execution.answer.complete_turn_latency_ms >= execution.search.latency_ms
        assert execution.answer.execution["terminal"]["outcome"] in {
            "insufficient_evidence",
            "verification_failed",
        }
        assert not execution.answer.generation_ran
    assert len(calls) == 2


@pytest.mark.parametrize(
    "outcome,reason,credit",
    [
        ("timed_out", "request_deadline_exceeded", False),
        ("verification_failed", "claim_verification_failed", False),
        ("insufficient_evidence", "no_retrieval_results", True),
    ],
)
def test_failed_execution_cannot_earn_negative_abstention_credit(outcome, reason, credit):
    from app.modules.evaluation.metrics import completed_abstention, execution_failed
    from app.modules.evaluation.services.evaluation_runner_service import _failed_cases

    row = {
        "expected_no_answer": True,
        "insufficient_evidence_reason": reason,
        "execution": {"terminal": {"outcome": outcome}},
        "kind": "no_answer",
        "case_key": "negative",
        "grounded": False,
        "citation_coverage": 0.0,
        "answer_token_coverage": 1.0,
    }
    assert completed_abstention(row) is credit
    assert execution_failed(row) is (not credit)
    assert bool(_failed_cases([row])) is (not credit)
