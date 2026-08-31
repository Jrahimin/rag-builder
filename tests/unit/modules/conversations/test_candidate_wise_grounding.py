"""Phase 1 multilingual candidate-wise grounding regressions."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from app.core.config import ChatConfig
from app.modules.conversations.citation_snapshots import build_citation_snapshots
from app.modules.conversations.context_builder import ContextBuilder
from app.modules.conversations.grounding_service import GroundingService
from app.modules.conversations.ports import ContextChunk, EvidenceUnit
from app.modules.conversations.prompt_builder import PromptBuilder
from app.modules.conversations.prompts.registry import require_prompt_template
from app.modules.conversations.services.chat_service import _balanced_evidence
from app.platform.domain.content_hash import content_hash
from app.platform.domain.evidence_contracts import (
    RERANKER_RELEVANCE_CALIBRATION_ID,
    BranchContribution,
    BranchScoreType,
    QueryVariant,
    QueryVariantKind,
)

pytestmark = pytest.mark.unit

_PRODUCTION_BASELINE = (
    Path(__file__).resolve().parents[3]
    / "fixtures/evaluation/phase1_multilingual_grounding_production_shape_v1.json"
)


def test_production_baseline_fixture_is_sanitized_and_complete() -> None:
    payload = json.loads(_PRODUCTION_BASELINE.read_text(encoding="utf-8"))

    assert payload["capture"]["captured_read_only"] is True
    assert payload["capture"]["sanitized"] is True
    assert len(payload["candidates"]) == 5
    assert [item["rank"] for item in payload["candidates"]] == [1, 2, 3, 4, 5]
    assert all(len(item["content_hash"]) == 64 for item in payload["candidates"])
    assert all(item["bounded_excerpt"] for item in payload["candidates"])
    assert payload["turn"]["reranker_call_count"] == 1
    assert payload["turn"]["branch_candidate_counts"]["translated_lexical:bn"] == 0
    assert "project_id" not in payload["capture"]
    assert "conversation_id" not in payload["capture"]


def _variant(
    variant_id: str,
    text: str,
    language: str,
    *,
    translated: bool = False,
) -> QueryVariant:
    return QueryVariant(
        variant_id=variant_id,
        kind=QueryVariantKind.TRANSLATED if translated else QueryVariantKind.ORIGINAL,
        language=language,
        text=text,
        source_variant_id="original" if translated else None,
        translation_provider="test" if translated else None,
        translation_model="test" if translated else None,
        translation_prompt_version="v1" if translated else None,
    )


def _contribution(
    family: str,
    variant_id: str,
    *,
    raw_score: float = 7.0,
) -> BranchContribution:
    return BranchContribution(
        branch_id=f"{family}:target" if family.startswith("translated") else family,
        family=family,
        query_variant_id=variant_id,
        target_language="target" if family.startswith("translated") else None,
        rank=1,
        raw_score=raw_score,
        score_type=(
            BranchScoreType.KEYWORD_BM25
            if family.endswith("lexical")
            else BranchScoreType.COSINE_SIMILARITY
        ),
        rrf_score=0.01,
    )


def _candidate(
    content: str,
    *,
    variants: tuple[QueryVariant, ...],
    contributions: tuple[BranchContribution, ...],
    reranker_score: float = 0.8,
    semantic_score: float | None = 0.1,
    calibration_id: str | None = RERANKER_RELEVANCE_CALIBRATION_ID,
    passage: tuple[float, int, int] | None = None,
) -> ContextChunk:
    passage_score, passage_start, passage_end = passage or (None, None, None)
    return ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=2,
        content=content,
        score=reranker_score,
        filename="sanitized.pdf",
        chunk_hash=content_hash(content),
        semantic_score=semantic_score,
        rank_score=reranker_score,
        rerank_relevance_score=reranker_score,
        evidence_relevance_score=reranker_score,
        evidence_score_method="reranker_relevance",
        evidence_calibration_id=calibration_id,
        passage_semantic_score=passage_score,
        passage_char_start=passage_start,
        passage_char_end=passage_end,
        passage_score_method="bounded_token_max_v1" if passage else None,
        page_number=4,
        char_start=1000,
        char_end=1000 + len(content),
        query_variants=variants,
        branch_contributions=contributions,
        metadata={"rerank_status": "applied"},
    )


def _service(**config: object) -> GroundingService:
    return GroundingService(ChatConfig(candidate_wise_grounding_enabled=True, **config))


def test_english_query_admits_bangla_evidence_via_translated_lexical() -> None:
    original = _variant("original", "What are the source tax deduction categories?", "en")
    translated = _variant(
        "translated:bn",
        "উৎসে কর কর্তনের খাতগুলো কী কী",
        "bn",
        translated=True,
    )
    chunk = _candidate(
        "উৎসে কর কর্তনের খাতগুলো হলো সঞ্চয়পত্র এবং সম্পত্তি অধিগ্রহণ।",
        variants=(original, translated),
        contributions=(_contribution("translated_lexical", translated.variant_id),),
    )

    decision = _service().assess(original.text, [chunk], rerank_status="applied")

    assert decision.sufficient is True
    assert decision.candidate_assessments[0].corroboration_method == "translated_lexical"
    assert decision.admitted_units[0].query_variant_id == translated.variant_id


def test_bangla_query_admits_english_evidence_via_translated_lexical() -> None:
    original = _variant("original", "রিফান্ড নীতি কী", "bn")
    translated = _variant(
        "translated:en",
        "what is the customer refund policy",
        "en",
        translated=True,
    )
    chunk = _candidate(
        "The customer refund policy allows returns within thirty days.",
        variants=(original, translated),
        contributions=(_contribution("translated_lexical", translated.variant_id),),
    )

    decision = _service().assess(original.text, [chunk], rerank_status="applied")

    assert decision.sufficient is True
    assert decision.candidate_assessments[0].corroboration_method == "translated_lexical"


def test_lower_ranked_candidate_is_admitted_after_rank_one_fails() -> None:
    original = _variant("original", "What are the source tax deduction categories?", "en")
    translated = _variant(
        "translated:bn",
        "উৎসে কর কর্তনের খাতগুলো কী কী",
        "bn",
        translated=True,
    )
    unrelated = _candidate(
        "কোম্পানির মাতৃত্বকালীন ছুটির আবেদন ব্যবস্থাপকের অনুমোদন সাপেক্ষ।",
        variants=(original, translated),
        contributions=(_contribution("translated_lexical", translated.variant_id),),
        reranker_score=0.92,
    )
    relevant = _candidate(
        "উৎসে কর কর্তনের খাতগুলো হলো সঞ্চয়পত্র এবং সম্পত্তি অধিগ্রহণ।",
        variants=(original, translated),
        contributions=(_contribution("translated_lexical", translated.variant_id),),
        reranker_score=0.81,
    )

    decision = _service().assess(original.text, [unrelated, relevant], rerank_status="applied")

    assert decision.sufficient is True
    assert decision.winning_chunk_id == relevant.chunk_id
    assert [item.terminal_reason for item in decision.candidate_assessments] == [
        "no_aligned_independent_signal",
        "admitted",
    ]


@pytest.mark.parametrize(
    ("query", "translated_text", "content"),
    [
        (
            "source tax কর্তনের categories কী",
            "উৎসে কর কর্তনের খাতগুলো কী",
            "উৎসে কর কর্তনের খাতগুলো সঞ্চয়পত্র ও সম্পত্তি অধিগ্রহণ।",
        ),
        (
            "source tax deduction er khat gulo ki",
            "উৎসে কর কর্তনের খাতগুলো কী",
            "উৎসে কর কর্তনের খাতগুলো সঞ্চয়পত্র ও সম্পত্তি অধিগ্রহণ।",
        ),
        (
            "section 163 tax rate 15 percent",
            "ধারা ১৬৩ করের হার ১৫ শতাংশ",
            "ধারা ১৬৩ করের হার ১৫ শতাংশ নির্ধারণ করে।",
        ),
        (
            "What is the refund period?",
            "Quelle est la période de remboursement",
            "La période de remboursement est de trente jours.",
        ),
    ],
)
def test_translated_lexical_handles_mixed_numeral_and_same_script_cases(
    query: str,
    translated_text: str,
    content: str,
) -> None:
    original = _variant("original", query, "und")
    translated = _variant("translated:target", translated_text, "target", translated=True)
    chunk = _candidate(
        content,
        variants=(original, translated),
        contributions=(_contribution("translated_lexical", translated.variant_id),),
    )

    assert _service().assess(query, [chunk], rerank_status="applied").sufficient is True


def test_punctuation_only_and_generic_overlap_do_not_admit() -> None:
    punctuation = _variant("original", "?! — ...", "und")
    punctuation_chunk = _candidate(
        "General tax information.",
        variants=(punctuation,),
        contributions=(_contribution("original_lexical", "original"),),
        semantic_score=None,
    )
    assert (
        _service().assess(punctuation.text, [punctuation_chunk], rerank_status="applied").sufficient
        is False
    )

    original = _variant("original", "source tax deduction category savings certificate", "en")
    translated = _variant(
        "translated:bn",
        "উৎসে কর কর্তনের খাত সঞ্চয়পত্র",
        "bn",
        translated=True,
    )
    generic = _candidate(
        "উৎসে করের সাধারণ হার এবং কোম্পানির হার।",
        variants=(original, translated),
        contributions=(_contribution("translated_lexical", translated.variant_id),),
        semantic_score=None,
    )
    decision = _service().assess(original.text, [generic], rerank_status="applied")
    assert decision.sufficient is False
    assert decision.candidate_assessments[0].terminal_reason == "no_aligned_independent_signal"


def test_provenance_alone_and_translated_dense_remain_non_admitting() -> None:
    original = _variant("original", "What are the source tax deduction categories?", "en")
    translated = _variant(
        "translated:bn",
        "উৎসে কর কর্তনের খাতগুলো কী কী",
        "bn",
        translated=True,
    )
    chunk = _candidate(
        "উৎসে কর কর্তনের খাতগুলো কী কী",
        variants=(original, translated),
        contributions=(_contribution("translated_dense", translated.variant_id, raw_score=0.91),),
        semantic_score=None,
    )
    chunk = replace(
        chunk,
        metadata={
            **chunk.metadata,
            "source_role": "primary",
            "source_relationships": [{"relationship_type": "modifies"}],
            "relationship_recall_provenance": [
                {"relationship_type": "modifies", "depth": 1}
            ],
            "relationship_grounding_trust": False,
        },
    )

    decision = _service().assess(original.text, [chunk], rerank_status="applied")

    assessment = decision.candidate_assessments[0]
    assert decision.sufficient is False
    assert assessment.translated_dense_shadow_scores == {translated.variant_id: 0.91}
    assert assessment.terminal_reason == "no_aligned_independent_signal"


def test_scored_passage_is_preferred_and_offsets_are_stable() -> None:
    query = "customer refund period"
    original = _variant("original", query, "en")
    content = "Unrelated preface. Customer refund period is thirty days. Unrelated appendix."
    start = content.index("Customer")
    end = content.index(". Unrelated") + 1
    chunk = _candidate(
        content,
        variants=(original,),
        contributions=(_contribution("original_dense", original.variant_id),),
        semantic_score=0.1,
        passage=(0.36, start, end),
    )

    decision = _service().assess(query, [chunk], rerank_status="applied")

    unit = decision.admitted_units[0]
    assert unit.span_derivation == "scored_passage"
    assert (unit.evidence_char_start, unit.evidence_char_end) == (start, end)
    assert unit.content == content[start:end]
    assert (unit.char_start, unit.char_end) == (1000 + start, 1000 + end)


def test_oversized_chunk_uses_safe_match_local_span_without_semantic_relabeling() -> None:
    original = _variant("original", "What are the source tax deduction categories?", "en")
    translated = _variant(
        "translated:bn",
        "উৎসে কর কর্তনের খাতগুলো কী কী",
        "bn",
        translated=True,
    )
    content = (
        "অপ্রাসঙ্গিক ভূমিকা। " * 80
        + "উৎসে কর কর্তনের খাতগুলো হলো সঞ্চয়পত্র এবং সম্পত্তি অধিগ্রহণ। "
        + "অপ্রাসঙ্গিক পরিশিষ্ট। " * 80
    )
    chunk = _candidate(
        content,
        variants=(original, translated),
        contributions=(_contribution("translated_lexical", translated.variant_id),),
        semantic_score=0.99,
    )

    decision = _service(context_char_budget=500).assess(
        original.text,
        [chunk],
        rerank_status="applied",
    )

    unit = decision.admitted_units[0]
    assessment = decision.candidate_assessments[0]
    assert unit.span_derivation == "match_local_sentence_v1"
    assert len(unit.content) <= 500
    assert unit.semantic_score is None
    assert assessment.semantic_span_aligned is False
    assert assessment.original_semantic_score is None


def test_oversized_chunk_without_an_anchor_is_rejected() -> None:
    original = _variant("original", "customer refund period", "en")
    chunk = _candidate(
        "Unrelated payroll appendix. " * 100,
        variants=(original,),
        contributions=(_contribution("original_dense", original.variant_id),),
        semantic_score=0.99,
    )

    decision = _service(context_char_budget=500).assess(
        original.text,
        [chunk],
        rerank_status="applied",
    )

    assert decision.sufficient is False
    assert decision.candidate_assessments[0].terminal_reason == "no_safe_evidence_span"


def test_calibration_mismatch_and_missing_variant_provenance_are_diagnosed() -> None:
    original = _variant("original", "customer refund period", "en")
    mismatch = _candidate(
        "Customer refund period is thirty days.",
        variants=(original,),
        contributions=(_contribution("original_dense", original.variant_id),),
        semantic_score=0.5,
        calibration_id="reranker_relevance:v999",
    )
    rejected = _service().assess(original.text, [mismatch], rerank_status="applied")
    assert rejected.candidate_assessments[0].terminal_reason == "calibration_mismatch"

    missing = _candidate(
        "Customer refund period is thirty days.",
        variants=(),
        contributions=(),
        semantic_score=0.5,
        calibration_id=None,
    )
    compatible = _service().assess(original.text, [missing], rerank_status="applied")
    assert compatible.sufficient is True
    assert compatible.candidate_assessments[0].query_variant_provenance_missing is True
    assert compatible.candidate_assessments[0].calibration_status == "missing_compatibility"


async def test_evidence_unit_identity_survives_prompt_citation_and_claim_verification() -> None:
    question = "What is the customer refund period?"
    content = "Customer refund period is thirty days after purchase."
    original = _variant("original", question, "en")
    chunk = _candidate(
        content,
        variants=(original,),
        contributions=(_contribution("original_dense", original.variant_id),),
        semantic_score=0.5,
    )
    service = _service()
    decision = service.assess(question, [chunk], rerank_status="applied")
    unit = decision.admitted_units[0]

    assert unit.corroboration_method == "original_semantic"
    selected = ContextBuilder(ChatConfig()).select([unit])
    assert selected[0] is unit
    prompt = PromptBuilder().build(
        template=require_prompt_template("v5"),
        context_chunks=selected,
        history=[],
        user_question=question,
    )[0].content
    assert unit.content in prompt
    assert unit.evidence_unit_id in prompt
    assert unit.evidence_span_hash in prompt

    citation = build_citation_snapshots(
        selected,
        config=ChatConfig(),
        project_id=uuid.uuid4(),
        config_snapshot_id=None,
        config_provenance={},
        prompt_version="v5",
    )[0]
    claims = await service.map_claims(f"{content} [1]", selected)
    claim_evidence = claims.claims[0]["evidence"][0]
    assert citation["evidence_unit_id"] == unit.evidence_unit_id
    assert citation["evidence_span_hash"] == unit.evidence_span_hash
    assert citation["chunk_hash"] == unit.evidence_span_hash
    assert claim_evidence["evidence_unit_id"] == unit.evidence_unit_id
    assert claim_evidence["evidence_span_hash"] == unit.evidence_span_hash


def test_context_budget_omits_an_evidence_unit_instead_of_truncating_it() -> None:
    unit = EvidenceUnit(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content="x" * 600,
        score=0.8,
        filename="sanitized.pdf",
        chunk_hash=content_hash("x" * 600),
        evidence_unit_id="unit",
        source_chunk_hash="source",
        evidence_span_hash=content_hash("x" * 600),
        evidence_char_start=0,
        evidence_char_end=600,
    )

    assert ContextBuilder(ChatConfig(context_char_budget=500)).select([unit]) == []


def test_combined_web_budget_never_truncates_an_evidence_unit() -> None:
    evidence_text = "k" * 400
    unit = EvidenceUnit(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=evidence_text,
        score=0.8,
        filename="knowledge.txt",
        chunk_hash=content_hash(evidence_text),
        evidence_unit_id="unit",
        source_chunk_hash="source",
        evidence_span_hash=content_hash(evidence_text),
        evidence_char_start=0,
        evidence_char_end=len(evidence_text),
    )
    web = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content="w" * 400,
        score=0.0,
        filename="web",
        chunk_hash="web",
        metadata={"source_kind": "web"},
    )
    config = ChatConfig(context_char_budget=500, max_context_chunks=2)

    selected = _balanced_evidence(
        [unit],
        [web],
        ContextBuilder(config),
        config,
    )

    assert selected[0] is unit
    assert selected[0].content == evidence_text
    assert content_hash(selected[0].content) == unit.evidence_span_hash
