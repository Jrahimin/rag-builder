"""Evaluation groundedness stays separate from production semantic admission."""

from __future__ import annotations

import uuid

import pytest

from app.composition.evaluation import GroundedEvaluationAnswerAdapter
from app.core.config import Settings
from app.modules.evaluation.ports import QualityHit
from app.platform.providers.implementations.echo_chat import EchoLLMProvider

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_CONTENT = "Cobalt escalation matrix assigns urgent incidents to Reliability."


def _adapter() -> GroundedEvaluationAnswerAdapter:
    return GroundedEvaluationAnswerAdapter(
        settings=Settings(),
        llm=EchoLLMProvider(model="echo-test", provider_version="1"),
    )


def _lexical_hit(*, semantic_score: float | None) -> QualityHit:
    return QualityHit(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        content=_CONTENT,
        score=0.71,
        filename="policy.txt",
        chunk_index=0,
        semantic_score=semantic_score,
        rank_score=0.71,
        rerank_relevance_score=0.71,
        metadata={"rerank_status": "applied"},
    )


async def test_reranked_lexical_evaluation_is_grounded_without_semantic_score() -> None:
    hit = _lexical_hit(semantic_score=None)
    answer = await _adapter().answer(
        profile="reranked_lexical",
        question="cobalt escalation matrix",
        hits=[hit],
    )

    assert answer.insufficient_evidence_reason is None
    assert answer.generation_ran is True
    assert answer.grounded is True
    assert answer.citation_coverage == 0.0
    assert answer.claims
    assert answer.claims[0]["evidence"][0]["chunk_id"] == str(hit.chunk_id)
    assert answer.claims[0]["verification"] == "supported"


async def test_reranked_lexical_evaluation_still_refuses_unrelated_retrieved_chunk() -> None:
    hit = _lexical_hit(semantic_score=None)
    answer = await _adapter().answer(
        profile="reranked_lexical",
        question="What is the lunar payroll rule?",
        hits=[hit],
    )

    assert answer.generation_ran is False
    assert answer.grounded is False
    assert answer.claims == []
    assert answer.insufficient_evidence_reason is not None
