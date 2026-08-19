"""Compare evidence-gate policies on recorded Gazette retrieval outcomes.

The hits below encode the live observation that relevant Bangla/OCR chunks can
score in the low 0.3x cosine range. Translation is present as a branch
contribution, not as the gate signal. No reranker is applied.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.composition.evaluation import GroundedEvaluationAnswerAdapter
from app.core.config import EvidenceGateMode, Settings
from app.modules.evaluation.ports import QualityHit
from app.modules.evaluation.schemas.evaluation import EvaluationCase, EvaluationDatasetCreate
from app.modules.evaluation.services.evaluation_runner_service import _case_result
from app.platform.providers.implementations.echo_chat import EchoLLMProvider

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_FIXTURE = Path("tests/fixtures/evaluation/ocr_gazette_grounding_v1.json")
_TABLE_BODY = "সঞ্চয়পত্র হইতে অর্জিত মুনাফা সম্পত্তির অধিগ্রহণ হস্তান্তর রপ্তানির বিপরীতে প্রাপ্ত নগদ ভর্তুকি মোটরযান"
_WRONG_SECTION = "ধারা ১৬৩ অনিবন্ধিত রপ্তানিকারকের জন্য ফৌজদারি দণ্ড নির্ধারণ করে।"
_WRONG_TABLE = "মূল্য সংযোজন কর VAT অধ্যায় পণ্যের শ্রেণিবিন্যাস তালিকা।"
_UNRELATED = "The lunar payroll calendar is published in April."
_TABLE_ID = uuid.UUID("10000000-0000-4000-8000-000000000001")
_DOCUMENT = uuid.UUID("20000000-0000-4000-8000-000000000001")
_POLICIES = (
    ("enforce_0.35", EvidenceGateMode.ENFORCE, 0.35),
    ("enforce_0.30", EvidenceGateMode.ENFORCE, 0.30),
    ("observe_0.35", EvidenceGateMode.OBSERVE, 0.35),
)


def _hit(
    chunk_id: uuid.UUID,
    content: str,
    *,
    semantic: float,
    rank_score: float,
    families: tuple[str, ...],
) -> QualityHit:
    return QualityHit(
        chunk_id=chunk_id,
        document_id=_DOCUMENT,
        content=content,
        score=rank_score,
        filename="gazette.pdf",
        chunk_index=0,
        semantic_score=semantic,
        rank_score=rank_score,
        metadata={
            "rrf_contributions": [
                {
                    "family": family,
                    "rank": index,
                    "rrf": rank_score / max(len(families), 1),
                }
                for index, family in enumerate(families, start=1)
            ]
        },
    )


def _table_hits() -> list[QualityHit]:
    return [
        _hit(
            _TABLE_ID,
            _TABLE_BODY,
            semantic=0.32,
            rank_score=0.018,
            families=("original_dense", "original_lexical", "translated_dense"),
        ),
        _hit(
            uuid.UUID("10000000-0000-4000-8000-000000000099"),
            _WRONG_SECTION,
            semantic=0.28,
            rank_score=0.012,
            families=("original_dense",),
        ),
    ]


def _recorded_hits(key: str) -> list[QualityHit]:
    if key.startswith("gazette.table."):
        return _table_hits()
    if key == "gazette.decoy.act12-section163":
        return [
            _hit(
                uuid.UUID("10000000-0000-4000-8000-000000000163"),
                _WRONG_SECTION,
                semantic=0.27,
                rank_score=0.014,
                families=("original_dense",),
            )
        ]
    if key == "gazette.decoy.wrong-table-vat":
        return [
            _hit(
                uuid.UUID("10000000-0000-4000-8000-0000000000aa"),
                _WRONG_TABLE,
                semantic=0.26,
                rank_score=0.013,
                families=("original_dense",),
            )
        ]
    return [
        _hit(
            uuid.UUID("10000000-0000-4000-8000-0000000000bb"),
            _UNRELATED,
            semantic=0.11,
            rank_score=0.008,
            families=("original_dense",),
        )
    ]


def _chat_config(*, mode: EvidenceGateMode, threshold: float):
    base = Settings().chat
    return base.model_copy(
        update={
            "evidence_gate_mode": mode,
            "minimum_semantic_evidence_score": threshold,
            "lexical_corroboration_floor_score": min(
                base.lexical_corroboration_floor_score,
                threshold,
            ),
            "system_prompt_version": "v4",
        }
    )


def _adapter(mode: EvidenceGateMode, threshold: float) -> GroundedEvaluationAnswerAdapter:
    settings = Settings().model_copy(update={"chat": _chat_config(mode=mode, threshold=threshold)})
    return GroundedEvaluationAnswerAdapter(
        settings=settings,
        llm=EchoLLMProvider(model="echo-test", provider_version="1"),
    )


def _required_cases() -> dict[str, EvaluationCase]:
    dataset = EvaluationDatasetCreate.model_validate(
        json.loads(_FIXTURE.read_text(encoding="utf-8"))
    )
    wanted = {
        "gazette.table.bn.exact",
        "gazette.table.en.cross-lingual",
        "gazette.table.banglish",
        "gazette.decoy.act12-section163",
        "gazette.decoy.wrong-table-vat",
        "gazette.decoy.unrelated",
    }
    cases = {case.key: case for case in dataset.cases if case.key in wanted}
    assert wanted <= set(cases)
    return cases


def _search(hits: list[QualityHit]) -> SimpleNamespace:
    return SimpleNamespace(
        hits=hits,
        latency_ms=12,
        rerank_status="passthrough",
        reranker_provider=None,
        reranker_model=None,
        reranker_version=None,
        reranker_score_scale=None,
        provenance={},
    )


async def test_gazette_gate_comparison_isolates_false_refusals_from_selection() -> None:
    cases = _required_cases()
    rows: list[dict[str, Any]] = []
    for key, case in cases.items():
        hits = _recorded_hits(key)
        for policy_name, mode, threshold in _POLICIES:
            answer = await _adapter(mode, threshold).answer(
                profile="hybrid",
                question=case.query,
                hits=hits,
            )
            result = _case_result(case, "hybrid", _search(hits), answer)
            result["policy"] = policy_name
            rows.append(result)

    positives = [row for row in rows if not row["expected_no_answer"]]
    negatives = [row for row in rows if row["expected_no_answer"]]

    assert all(row["relevant_in_retrieved"] for row in positives)
    assert all(row["relevant_in_selected"] for row in positives)
    assert all(row["relevant_dropped_before_gate"] is False for row in positives)
    assert all(row["relevant_chunk_rank"] == 1 for row in positives)
    assert all(row["translated_branch_contributed"] for row in positives)
    assert {row["query_language"] for row in positives} >= {"bn", "en"}

    enforce_035 = [row for row in positives if row["policy"] == "enforce_0.35"]
    enforce_030 = [row for row in positives if row["policy"] == "enforce_0.30"]
    observe = [row for row in positives if row["policy"] == "observe_0.35"]

    assert all(row["generation_ran"] is False for row in enforce_035)
    assert all(row["evidence_gate_sufficient"] is False for row in enforce_035)
    assert all(row["evidence_score"] == pytest.approx(0.32) for row in enforce_035)
    assert all(row["winning_semantic_score"] == pytest.approx(0.32) for row in enforce_035)
    assert all(row["winning_rank_score"] == pytest.approx(0.018) for row in enforce_035)
    assert all(row["evidence_score"] != row["winning_rank_score"] for row in enforce_035)

    assert all(row["generation_ran"] is True for row in enforce_030)
    assert all(row["evidence_gate_sufficient"] is True for row in enforce_030)
    assert all(row["insufficient_evidence_reason"] is None for row in enforce_030)

    assert all(row["generation_ran"] is True for row in observe)
    assert all(row["evidence_gate_sufficient"] is False for row in observe)
    assert all(row["evidence_gate_reason"] == "below_relevance_threshold" for row in observe)
    assert all(row["insufficient_evidence_reason"] is None for row in observe)
    assert all(row["answer"].startswith("[echo]") for row in observe)

    negative_enforce = [row for row in negatives if row["policy"].startswith("enforce_")]
    assert all(row["generation_ran"] is False for row in negative_enforce)
    assert all(row["insufficient_evidence_reason"] is not None for row in negative_enforce)

    observe_negatives = [row for row in negatives if row["policy"] == "observe_0.35"]
    assert all(row["generation_ran"] is True for row in observe_negatives)
    assert all(row["evidence_gate_sufficient"] is False for row in observe_negatives)
    assert all(
        row["evidence_gate_reason"] == "below_relevance_threshold" for row in observe_negatives
    )
    assert all(row["grounded"] is False for row in observe_negatives)
