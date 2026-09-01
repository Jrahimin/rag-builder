"""Strict vs balanced grounding, adaptive passage rescue, and Bangla scaffolding."""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from app.core.config import ChatConfig, GroundingMode, RerankerProviderConfig, RetrievalConfig
from app.modules.conversations.context_builder import ContextBuilder
from app.modules.conversations.grounded_context import assess_and_select_knowledge
from app.modules.conversations.grounding_service import GroundingService
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.schemas.message import InsufficientEvidenceReason
from app.platform.domain.content_hash import content_hash
from app.platform.domain.evidence_contracts import (
    RERANKER_RELEVANCE_CALIBRATION_ID,
    BranchContribution,
    BranchScoreType,
    QueryVariant,
    QueryVariantKind,
)
from app.platform.providers.contracts.embedding import (
    BaseEmbeddingProvider,
    EmbeddingBatchResult,
    EmbeddingPurpose,
)

pytestmark = pytest.mark.unit

_NEAR_MISS_QUERY = "How much rebate was available historically?"
_STRICT_EVIDENCE = (
    "The historical rebate available to individuals was fifteen percent for that year."
)
_NEAR_MISS_EVIDENCE = (
    "Office stationery rules occupy most of this chapter. "
    "Section 21 records that the figure stood at fifteen percent for that year. "
    "Parking permits are issued on Tuesdays only."
)
_UNRELATED_EVIDENCE = (
    "Office stationery rules occupy most of this chapter. "
    "Parking permits are issued on Tuesdays only and never mention rebates."
)


def _variant(text: str, language: str = "en") -> QueryVariant:
    return QueryVariant(
        variant_id="original",
        kind=QueryVariantKind.ORIGINAL,
        language=language,
        text=text,
    )


def _contribution() -> BranchContribution:
    return BranchContribution(
        branch_id="original_dense",
        family="original_dense",
        query_variant_id="original",
        target_language=None,
        rank=1,
        raw_score=0.2,
        score_type=BranchScoreType.COSINE_SIMILARITY,
        rrf_score=0.01,
    )


def _candidate(
    content: str,
    *,
    query: str = _NEAR_MISS_QUERY,
    reranker_score: float = 0.85,
    semantic_score: float | None = 0.30,
    calibration_id: str | None = RERANKER_RELEVANCE_CALIBRATION_ID,
    passage: tuple[float, int, int] | None = None,
    language: str = "en",
    extra_metadata: dict[str, object] | None = None,
) -> ContextChunk:
    passage_score, passage_start, passage_end = passage or (None, None, None)
    variant = _variant(query, language)
    return ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=2,
        content=content,
        score=reranker_score,
        filename="policy.pdf",
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
        char_start=0,
        char_end=len(content),
        query_variants=(variant,),
        branch_contributions=(_contribution(),),
        metadata={"rerank_status": "applied", **(extra_metadata or {})},
    )


def _service(**config: object) -> GroundingService:
    return GroundingService(ChatConfig(candidate_wise_grounding_enabled=True, **config))


class _NeedleEmbedder(BaseEmbeddingProvider):
    """Return a matching vector only when the needle appears in the text."""

    def __init__(self, needle: str) -> None:
        self._needle = needle
        self.document_calls = 0

    @property
    def provider_name(self) -> str:
        return "test"

    @property
    def model_name(self) -> str:
        return "needle"

    @property
    def dimensions(self) -> int:
        return 2

    @property
    def provider_version(self) -> str:
        return "1"

    async def embed_texts(
        self,
        texts: list[str],
        *,
        purpose: EmbeddingPurpose = EmbeddingPurpose.DOCUMENT,
    ) -> EmbeddingBatchResult:
        if purpose is EmbeddingPurpose.DOCUMENT:
            self.document_calls += len(texts)
        vectors = [
            [1.0, 0.0] if purpose is EmbeddingPurpose.QUERY or self._needle in text else [0.0, 1.0]
            for text in texts
        ]
        return EmbeddingBatchResult(
            vectors=vectors,
            provider=self.provider_name,
            model=self.model_name,
            dimensions=self.dimensions,
            provider_version=self.provider_version,
        )


def test_strict_rejects_high_reranker_near_miss() -> None:
    chunk = _candidate(_NEAR_MISS_EVIDENCE)

    decision = _service(grounding_mode=GroundingMode.STRICT).assess(
        _NEAR_MISS_QUERY,
        [chunk],
        rerank_status="applied",
    )

    assert decision.sufficient is False
    assert decision.candidate_assessments[0].terminal_reason == "no_aligned_independent_signal"


def test_balanced_admits_high_reranker_near_miss_with_safe_span_and_calibration() -> None:
    chunk = _candidate(_NEAR_MISS_EVIDENCE)

    decision = _service(grounding_mode=GroundingMode.BALANCED).assess(
        _NEAR_MISS_QUERY,
        [chunk],
        rerank_status="applied",
    )

    assert decision.sufficient is True
    assert decision.candidate_assessments[0].corroboration_method == "high_confidence_reranker"
    assert decision.candidate_assessments[0].span_derivation is not None


def test_balanced_still_rejects_low_confidence_near_miss() -> None:
    chunk = _candidate(_NEAR_MISS_EVIDENCE, reranker_score=0.55)

    decision = _service(grounding_mode=GroundingMode.BALANCED).assess(
        _NEAR_MISS_QUERY,
        [chunk],
        rerank_status="applied",
    )

    assert decision.sufficient is False
    assert decision.candidate_assessments[0].terminal_reason == "no_aligned_independent_signal"


def test_balanced_rejects_unrelated_high_reranker_without_near_miss() -> None:
    chunk = _candidate(_UNRELATED_EVIDENCE, reranker_score=0.92, semantic_score=0.10)

    decision = _service(grounding_mode=GroundingMode.BALANCED).assess(
        _NEAR_MISS_QUERY,
        [chunk],
        rerank_status="applied",
    )

    assert decision.sufficient is False
    assert decision.candidate_assessments[0].terminal_reason == "no_aligned_independent_signal"


def test_strict_rejects_unrelated_high_reranker() -> None:
    chunk = _candidate(_UNRELATED_EVIDENCE, reranker_score=0.92, semantic_score=0.10)

    decision = _service(grounding_mode=GroundingMode.STRICT).assess(
        _NEAR_MISS_QUERY,
        [chunk],
        rerank_status="applied",
    )

    assert decision.sufficient is False


def test_balanced_rejects_calibration_mismatch_even_on_near_miss() -> None:
    chunk = _candidate(_NEAR_MISS_EVIDENCE, calibration_id="other-reranker:v1")

    decision = _service(grounding_mode=GroundingMode.BALANCED).assess(
        _NEAR_MISS_QUERY,
        [chunk],
        rerank_status="applied",
    )

    assert decision.sufficient is False
    assert decision.candidate_assessments[0].terminal_reason == "calibration_mismatch"


def test_balanced_rejects_missing_safe_span() -> None:
    filler = " ".join(f"token-{index}" for index in range(200))
    chunk = _candidate(filler, semantic_score=0.30)

    decision = _service(
        grounding_mode=GroundingMode.BALANCED,
        context_char_budget=500,
    ).assess(
        _NEAR_MISS_QUERY,
        [chunk],
        rerank_status="applied",
    )

    assert len(filler) > 500
    assert decision.sufficient is False
    assert decision.candidate_assessments[0].terminal_reason == "no_safe_evidence_span"


def test_legacy_balanced_admits_the_same_high_confidence_near_miss() -> None:
    chunk = _candidate(_NEAR_MISS_EVIDENCE)
    service = GroundingService(ChatConfig(grounding_mode=GroundingMode.BALANCED))

    decision = service.assess(_NEAR_MISS_QUERY, [chunk], rerank_status="applied")

    assert decision.sufficient is True
    assert decision.evidence_score_method == "reranker_relevance"


async def test_passage_rescue_scores_only_high_confidence_near_misses() -> None:
    near_miss = _candidate(_NEAR_MISS_EVIDENCE)
    unrelated = _candidate(_UNRELATED_EVIDENCE, reranker_score=0.93, semantic_score=0.10)
    already_scored = _candidate(
        _NEAR_MISS_EVIDENCE,
        passage=(0.41, 10, 80),
    )
    embedder = _NeedleEmbedder("fifteen percent")
    config = ChatConfig(
        grounding_mode=GroundingMode.STRICT,
        candidate_wise_grounding_enabled=True,
    )
    grounding = GroundingService(config, embedder=embedder)

    evidence, selected = await assess_and_select_knowledge(
        grounding=grounding,
        context_builder=ContextBuilder(config),
        chat_config=config,
        question=_NEAR_MISS_QUERY,
        chunks=[near_miss, unrelated, already_scored],
        rerank_status="applied",
        retrieval_config=RetrievalConfig(
            passage_window_tokens=24,
            passage_overlap_tokens=8,
            passage_min_tokens=8,
        ),
    )

    assert evidence.passage_rescue_status == "applied"
    assert evidence.passage_rescue_candidate_count == 1
    assert evidence.sufficient is True
    assert near_miss.chunk_id in {chunk.chunk_id for chunk in selected} or any(
        assessment.chunk_id == near_miss.chunk_id and assessment.passed
        for assessment in evidence.candidate_assessments
    )
    rescued = next(
        item for item in evidence.candidate_assessments if item.chunk_id == near_miss.chunk_id
    )
    skipped = next(
        item for item in evidence.candidate_assessments if item.chunk_id == unrelated.chunk_id
    )
    preserved = next(
        item for item in evidence.candidate_assessments if item.chunk_id == already_scored.chunk_id
    )
    assert rescued.passed is True
    assert rescued.span_derivation == "scored_passage"
    assert skipped.passed is False
    assert preserved.span_derivation == "scored_passage"
    assert embedder.document_calls >= 1
    assert embedder.document_calls < 20


async def test_passage_rescue_does_not_replace_a_stronger_whole_chunk_score() -> None:
    chunk = _candidate(_NEAR_MISS_EVIDENCE, semantic_score=0.30)
    embedder = _NeedleEmbedder("this needle is absent")
    config = ChatConfig(
        grounding_mode=GroundingMode.STRICT,
        candidate_wise_grounding_enabled=True,
    )
    grounding = GroundingService(config, embedder=embedder)

    evidence, _selected = await assess_and_select_knowledge(
        grounding=grounding,
        context_builder=ContextBuilder(config),
        chat_config=config,
        question=_NEAR_MISS_QUERY,
        chunks=[chunk],
        rerank_status="applied",
        retrieval_config=RetrievalConfig(
            passage_window_tokens=24,
            passage_overlap_tokens=8,
            passage_min_tokens=8,
        ),
    )

    assert evidence.passage_rescue_status == "not_needed"
    assert evidence.sufficient is False
    assert evidence.candidate_assessments[0].span_derivation != "scored_passage"


def test_bangla_interrogative_scaffolding_is_not_counted_as_significant() -> None:
    query = "হার কত ছিল"
    chunk = _candidate(
        "হার ১৫%",
        query=query,
        reranker_score=0.81,
        semantic_score=0.10,
        language="bn",
    )

    decision = _service().assess(query, [chunk], rerank_status="applied")

    assert decision.sufficient is True
    assert decision.candidate_assessments[0].corroboration_method == "original_lexical"


def test_bangla_domain_tokens_are_not_treated_as_scaffolding() -> None:
    query = "বিনিয়োগ রিবেটের হার কত ছিল"
    chunk = _candidate(
        "পার্কিং নিয়ম মঙ্গলবার প্রযোজ্য",
        query=query,
        reranker_score=0.81,
        semantic_score=0.10,
        language="bn",
    )

    decision = _service().assess(query, [chunk], rerank_status="applied")

    assert decision.sufficient is False
    assert decision.candidate_assessments[0].terminal_reason == "no_aligned_independent_signal"


def test_default_reranker_timeout_is_ten_seconds() -> None:
    assert RerankerProviderConfig().request_timeout_seconds == 10.0


def test_default_chat_grounding_stays_strict() -> None:
    config = ChatConfig()

    assert config.grounding_mode is GroundingMode.STRICT
    assert config.high_confidence_reranker_evidence_score == pytest.approx(0.70)
    assert config.minimum_reranker_evidence_score == pytest.approx(0.40)


def test_high_confidence_bar_must_exceed_minimum_reranker_bar() -> None:
    with pytest.raises(ValueError, match="high_confidence_reranker_evidence_score"):
        ChatConfig(
            minimum_reranker_evidence_score=0.70,
            high_confidence_reranker_evidence_score=0.70,
        )


def test_strict_acceptance_implies_balanced_acceptance_on_the_same_candidates() -> None:
    chunks = [
        _candidate(_STRICT_EVIDENCE, semantic_score=0.41),
        _candidate(_UNRELATED_EVIDENCE, reranker_score=0.92, semantic_score=0.10),
    ]

    strict = _service(grounding_mode=GroundingMode.STRICT).assess(
        _NEAR_MISS_QUERY,
        chunks,
        rerank_status="applied",
    )
    balanced = _service(grounding_mode=GroundingMode.BALANCED).assess(
        _NEAR_MISS_QUERY,
        chunks,
        rerank_status="applied",
    )

    strict_ids = {unit.chunk_id for unit in strict.admitted_units}
    balanced_ids = {unit.chunk_id for unit in balanced.admitted_units}
    assert strict.sufficient is True
    assert strict_ids
    assert strict_ids <= balanced_ids
    assert all(
        assessment.passed is True
        for assessment in balanced.candidate_assessments
        if assessment.chunk_id in strict_ids
    )


def test_balanced_high_confidence_rescue_is_additive() -> None:
    strict_chunk = _candidate(_STRICT_EVIDENCE, semantic_score=0.41)
    near_miss = _candidate(_NEAR_MISS_EVIDENCE)
    chunks = [near_miss, strict_chunk]

    strict = _service(grounding_mode=GroundingMode.STRICT).assess(
        _NEAR_MISS_QUERY,
        chunks,
        rerank_status="applied",
    )
    balanced = _service(grounding_mode=GroundingMode.BALANCED).assess(
        _NEAR_MISS_QUERY,
        chunks,
        rerank_status="applied",
    )

    strict_ids = {unit.chunk_id for unit in strict.admitted_units}
    balanced_ids = {unit.chunk_id for unit in balanced.admitted_units}
    assert strict_ids == {strict_chunk.chunk_id}
    assert balanced_ids == {strict_chunk.chunk_id, near_miss.chunk_id}
    near_miss_assessment = next(
        item for item in balanced.candidate_assessments if item.chunk_id == near_miss.chunk_id
    )
    assert near_miss_assessment.corroboration_method == "high_confidence_reranker"
    assert balanced.winning_chunk_id == strict_chunk.chunk_id


def test_passage_rescue_cannot_regress_already_admitted_evidence() -> None:
    admitted = _candidate(_STRICT_EVIDENCE, semantic_score=0.41)
    near_miss = _candidate(_NEAR_MISS_EVIDENCE)
    service = _service(grounding_mode=GroundingMode.STRICT)
    before = service.assess_candidate_wise(
        _NEAR_MISS_QUERY,
        [admitted, near_miss],
        rerank_status="applied",
    )
    degraded = replace(admitted, content=_UNRELATED_EVIDENCE, semantic_score=0.10)
    after = service.assess_candidate_wise(
        _NEAR_MISS_QUERY,
        [degraded, near_miss],
        rerank_status="applied",
    )
    merged = service.merge_monotonic_admissions(before, after)

    assert before.sufficient is True
    assert admitted.chunk_id in {unit.chunk_id for unit in before.admitted_units}
    assert admitted.chunk_id not in {unit.chunk_id for unit in after.admitted_units}
    assert admitted.chunk_id in {unit.chunk_id for unit in merged.admitted_units}
    kept = next(item for item in merged.candidate_assessments if item.chunk_id == admitted.chunk_id)
    assert kept.passed is True
    assert kept.corroboration_method in {"original_lexical", "original_semantic"}


def _authority_chunks() -> tuple[ContextChunk, ContextChunk, list[dict[str, object]]]:
    base_revision = uuid.uuid4()
    modifier_revision = uuid.uuid4()
    records = [
        {
            "outcome": "already_in_recall",
            "base_revision_id": str(base_revision),
            "modifier_revision_id": str(modifier_revision),
            "target_provisions": ["Section 21 — Investment Rebate Rate"],
        }
    ]
    metadata = {
        "source_revision_id": str(base_revision),
        "modifies_expansion_records": records,
    }
    base = _candidate(
        "Section 21 — Investment Rebate Rate\n"
        "The historical rebate available to individuals was fifteen percent.",
        semantic_score=0.41,
        extra_metadata=metadata,
    )
    modifier = _candidate(
        "Section 5 — Amendment\nThe current rebate available to individuals is ten percent.",
        semantic_score=0.41,
        extra_metadata={
            "source_revision_id": str(modifier_revision),
            "modifies_expansion_records": records,
        },
    )
    return base, modifier, records


async def test_authority_redacted_evidence_falls_through_to_another_valid_candidate() -> None:
    base, modifier, _records = _authority_chunks()
    config = ChatConfig(
        grounding_mode=GroundingMode.STRICT,
        candidate_wise_grounding_enabled=True,
    )
    evidence, selected = await assess_and_select_knowledge(
        grounding=GroundingService(config),
        context_builder=ContextBuilder(config),
        chat_config=config,
        question=_NEAR_MISS_QUERY,
        chunks=[base, modifier],
        rerank_status="applied",
    )

    assert evidence.sufficient is True
    assert evidence.reason is None
    assert evidence.usable_after_authority_count == 1
    assert {chunk.chunk_id for chunk in selected} == {modifier.chunk_id}
    assert evidence.winning_chunk_id == modifier.chunk_id
    assert any(
        item.chunk_id == base.chunk_id and item.passed for item in evidence.candidate_assessments
    )


async def test_no_valid_remaining_candidate_after_authority_still_refuses() -> None:
    base, _modifier, records = _authority_chunks()
    modifier_revision = str(records[0]["modifier_revision_id"])
    placeholder = _candidate(
        _UNRELATED_EVIDENCE,
        reranker_score=0.92,
        semantic_score=0.10,
        extra_metadata={
            "source_revision_id": modifier_revision,
            "modifies_expansion_records": records,
        },
    )
    config = ChatConfig(
        grounding_mode=GroundingMode.STRICT,
        candidate_wise_grounding_enabled=True,
    )
    evidence, selected = await assess_and_select_knowledge(
        grounding=GroundingService(config),
        context_builder=ContextBuilder(config),
        chat_config=config,
        question=_NEAR_MISS_QUERY,
        chunks=[base, placeholder],
        rerank_status="applied",
    )

    assert evidence.sufficient is False
    assert evidence.reason is InsufficientEvidenceReason.AUTHORITY_CONTEXT_EMPTY
    assert (
        GroundingService(config).diagnostics(
            evidence,
            blocked_generation=True,
            generation_ran=False,
        )["failure_stage"]
        == "context_selection"
    )
    assert any(item.passed for item in evidence.candidate_assessments)
    assert evidence.usable_after_authority_count == 0
    assert selected == []
