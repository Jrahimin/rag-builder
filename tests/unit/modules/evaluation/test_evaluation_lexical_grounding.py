"""Evaluation groundedness stays separate from production semantic admission."""

from __future__ import annotations

import uuid

import pytest

from app.composition.evaluation import GroundedEvaluationAnswerAdapter
from app.core.config import Settings
from app.modules.evaluation.ports import QualityHit
from app.platform.domain.evidence_contracts import (
    RERANKER_RELEVANCE_CALIBRATION_ID,
    BranchContribution,
    BranchScoreType,
    QueryVariant,
    QueryVariantKind,
)
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


async def test_reranked_lexical_evaluation_requires_citations_like_chat() -> None:
    hit = _lexical_hit(semantic_score=None)
    answer = await _adapter().answer(
        profile="reranked_lexical",
        question="cobalt escalation matrix",
        hits=[hit],
    )

    assert answer.insufficient_evidence_reason is None
    assert answer.generation_ran is True
    assert answer.grounded is False
    assert answer.citation_coverage == 0.0
    assert answer.claims
    assert answer.claims[0]["evidence"] == []
    assert answer.claims[0]["verification"] == "unsupported"


async def test_candidate_wise_evaluation_prompts_admitted_evidence_units() -> None:
    original_text = "What is the cobalt escalation matrix?"
    relevant = "The cobalt escalation matrix assigns urgent incidents to Reliability."
    decoy = "Maternity leave requests require manager approval."
    original = QueryVariant(
        variant_id="original",
        kind=QueryVariantKind.ORIGINAL,
        language="en",
        text=original_text,
    )
    relevant_hit = QualityHit(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        content=relevant,
        score=0.82,
        filename="policy.txt",
        chunk_index=1,
        semantic_score=0.12,
        rank_score=0.82,
        rerank_relevance_score=0.82,
        evidence_calibration_id=RERANKER_RELEVANCE_CALIBRATION_ID,
        query_variants=(original,),
        branch_contributions=(
            BranchContribution(
                branch_id="original_lexical",
                family="original_lexical",
                query_variant_id=original.variant_id,
                target_language=None,
                rank=1,
                raw_score=8.1,
                score_type=BranchScoreType.KEYWORD_BM25,
                rrf_score=0.02,
            ),
        ),
        metadata={"rerank_status": "applied"},
    )
    decoy_hit = QualityHit(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        content=decoy,
        score=0.91,
        filename="policy.txt",
        chunk_index=0,
        semantic_score=0.11,
        rank_score=0.91,
        rerank_relevance_score=0.91,
        evidence_calibration_id=RERANKER_RELEVANCE_CALIBRATION_ID,
        query_variants=(original,),
        branch_contributions=(
            BranchContribution(
                branch_id="original_dense",
                family="original_dense",
                query_variant_id=original.variant_id,
                target_language=None,
                rank=1,
                raw_score=0.11,
                score_type=BranchScoreType.COSINE_SIMILARITY,
                rrf_score=0.016,
            ),
        ),
        metadata={"rerank_status": "applied"},
    )
    adapter = GroundedEvaluationAnswerAdapter(
        settings=Settings(),
        llm=EchoLLMProvider(model="echo-test", provider_version="1"),
    )

    answer = await adapter.answer(
        profile="reranked_lexical",
        question=original_text,
        hits=[decoy_hit, relevant_hit],
    )

    assert answer.generation_ran is True
    assert answer.selected_chunk_ids == [relevant_hit.chunk_id]
    assert answer.evidence_gate["candidate_wise"]["path"] == "candidate_wise"
    assert answer.evidence_gate["candidate_wise"]["admitted_count"] == 1
    assert answer.evidence_gate["candidate_wise"]["assessed_count"] == 2


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
