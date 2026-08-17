"""Unit tests for evidence sufficiency and claim-to-source mapping."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import ChatConfig, EvidenceScoreMode
from app.modules.conversations.grounding_service import GroundingService
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.schemas.message import InsufficientEvidenceReason

pytestmark = pytest.mark.unit


def _chunk(
    *,
    content: str,
    score: float = 0.02,
    semantic_score: float | None = 0.9,
    passage_semantic_score: float | None = None,
    passage_char_start: int | None = None,
    passage_char_end: int | None = None,
) -> ContextChunk:
    return ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=2,
        content=content,
        score=score,
        filename="policy.pdf",
        chunk_hash="hash",
        semantic_score=semantic_score,
        passage_semantic_score=passage_semantic_score,
        passage_char_start=passage_char_start,
        passage_char_end=passage_char_end,
        passage_score_method=(
            "bounded_token_max_v1" if passage_semantic_score is not None else None
        ),
        page_number=4,
        char_start=120,
        char_end=240,
    )


def test_no_results_is_an_explicit_insufficient_evidence_outcome() -> None:
    decision = GroundingService(ChatConfig()).assess("unsupported question", [])
    assert decision.sufficient is False
    assert decision.reason is InsufficientEvidenceReason.NO_RETRIEVAL_RESULTS


def test_deprecated_evidence_gate_settings_fail_fast() -> None:
    with pytest.raises(ValueError, match=r"MINIMUM_EVIDENCE_SCORE.*deprecated"):
        ChatConfig(minimum_evidence_score=0.01)


def test_equal_semantic_bar_and_rescue_floor_is_allowed() -> None:
    config = ChatConfig(
        minimum_semantic_evidence_score=0.35,
        lexical_corroboration_floor_score=0.35,
    )
    assert config.lexical_corroboration_floor_score == config.minimum_semantic_evidence_score


def test_default_rescue_floor_is_below_the_semantic_bar() -> None:
    config = ChatConfig()
    assert config.lexical_corroboration_floor_score < config.minimum_semantic_evidence_score


def test_high_overlap_score_just_below_the_bar_is_rescued() -> None:
    """Real Bangla gazette tables scored ~0.34 with ~0.8 query-token coverage."""
    decision = GroundingService(ChatConfig()).assess(
        "উৎসে কর সংগ্রহের খাত কি?",
        [
            _chunk(
                content=(
                    "সারণী আয়ের উৎস/উৎসে কর সংগ্রহের খাত সঞ্চয়পত্র হইতে অর্জিত মুনাফা "
                    "সম্পত্তির অধিগ্রহণ রপ্তানির বিপরীতে প্রাপ্ত নগদ ভর্তুকি"
                ),
                semantic_score=0.340,
            )
        ],
    )
    assert decision.sufficient is True
    assert decision.lexically_corroborated is True
    assert decision.reason is None


def test_rescue_floor_cannot_exceed_semantic_bar() -> None:
    with pytest.raises(ValueError, match="must not exceed"):
        ChatConfig(
            minimum_semantic_evidence_score=0.35,
            lexical_corroboration_floor_score=0.4,
        )


def test_low_semantic_relevance_declines_unrelated_evidence() -> None:
    service = GroundingService(
        ChatConfig(
            minimum_semantic_evidence_score=0.5,
            lexical_corroboration_floor_score=0.3,
            lexical_corroboration_coverage=0.5,
        )
    )
    decision = service.assess(
        "What is the lunar payroll rule?",
        [
            _chunk(
                content="Customer refunds are available for thirty days.",
                score=0.9,
                semantic_score=0.2,
            )
        ],
    )
    assert decision.sufficient is False
    assert decision.reason is InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD


@pytest.mark.parametrize(
    ("question", "content"),
    [
        ("refund policy", "unrelated vocabulary"),
        ("refund policy", "refund unrelated"),
        ("refund policy", "refund policy"),
        ("refund policy identifier RF-30", "refund policy identifier RF-30 applies"),
    ],
)
def test_lexical_coverage_never_rejects_semantically_relevant_evidence(
    question: str,
    content: str,
) -> None:
    service = GroundingService(ChatConfig(minimum_semantic_evidence_score=0.5))

    decision = service.assess(
        question,
        [_chunk(content=content, score=0.0001, semantic_score=0.8)],
    )

    assert decision.sufficient is True
    assert decision.reason is None


def test_low_query_evidence_coverage_is_never_emitted() -> None:
    service = GroundingService(
        ChatConfig(
            minimum_semantic_evidence_score=0.5,
            lexical_corroboration_floor_score=0.3,
            lexical_corroboration_coverage=0.5,
        )
    )
    decisions = [
        service.assess("unsupported question", []),
        service.assess(
            "refund policy",
            [_chunk(content="refund policy", score=1.0, semantic_score=None)],
        ),
        service.assess(
            "refund policy",
            [_chunk(content="unrelated vocabulary", semantic_score=0.2)],
        ),
        service.assess(
            "refund policy",
            [_chunk(content="refund policy applies", semantic_score=0.4)],
        ),
        service.assess(
            "refund policy",
            [_chunk(content="unrelated vocabulary", semantic_score=0.8)],
        ),
    ]

    assert all(
        decision.reason is not InsufficientEvidenceReason.LOW_QUERY_EVIDENCE_COVERAGE
        for decision in decisions
    )


def test_lexical_corroboration_only_rescues_above_semantic_floor() -> None:
    service = GroundingService(
        ChatConfig(
            minimum_semantic_evidence_score=0.6,
            lexical_corroboration_floor_score=0.3,
            lexical_corroboration_coverage=0.5,
        )
    )

    rescued = service.assess(
        "refund policy",
        [_chunk(content="refund policy applies", semantic_score=0.4)],
    )
    rejected = service.assess(
        "refund policy",
        [_chunk(content="refund policy applies", semantic_score=0.2)],
    )

    assert rescued.sufficient is True
    assert rescued.lexically_corroborated is True
    assert rejected.sufficient is False
    assert rejected.reason is InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD


def test_lexical_rescue_cannot_combine_score_and_tokens_from_different_chunks() -> None:
    service = GroundingService(
        ChatConfig(
            minimum_semantic_evidence_score=0.6,
            lexical_corroboration_floor_score=0.3,
            lexical_corroboration_coverage=1.0,
        )
    )
    high_score = _chunk(content="unrelated material", semantic_score=0.4)
    matching_words = _chunk(content="refund policy", semantic_score=0.2)

    decision = service.assess("refund policy", [high_score, matching_words])

    assert decision.sufficient is False
    assert decision.winning_chunk_id == high_score.chunk_id


def test_passage_mode_uses_raw_passage_score_and_local_span() -> None:
    content = "unrelated preface refund policy applies unrelated appendix"
    start = content.index("refund")
    end = content.index(" unrelated appendix")
    chunk = _chunk(
        content=content,
        semantic_score=0.2,
        passage_semantic_score=0.7,
        passage_char_start=start,
        passage_char_end=end,
    )
    service = GroundingService(
        ChatConfig(
            evidence_score_mode=EvidenceScoreMode.PASSAGE_MAX,
            minimum_semantic_evidence_score=0.6,
        )
    )

    decision = service.assess("refund policy", [chunk])

    assert decision.sufficient is True
    assert decision.best_score == 0.7
    assert decision.evidence_score_method == "bounded_token_max_v1"
    assert decision.evidence_char_start == start
    assert decision.evidence_char_end == end


def test_missing_semantic_score_never_uses_ranking_score_as_evidence() -> None:
    decision = GroundingService(ChatConfig()).assess(
        "refund policy",
        [_chunk(content="refund policy", score=1.0, semantic_score=None)],
    )

    assert decision.sufficient is False
    assert decision.reason is InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD


def test_claims_link_to_cited_page_and_offsets() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))
    chunk = _chunk(content="Customer refunds are available for thirty days.")
    result = service.map_claims("Refunds are available for thirty days. [1]", [chunk])
    assert result.grounded is True
    assert result.claims[0]["verification"] == "supported"
    assert result.citation_coverage == 1.0
    evidence = result.claims[0]["evidence"][0]
    assert evidence["chunk_id"] == str(chunk.chunk_id)
    assert evidence["page_number"] == 4
    assert evidence["char_start"] == 120
    assert evidence["char_end"] == 240


def test_supported_claim_without_numbered_citation_has_zero_citation_coverage() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))
    chunk = _chunk(content="Customer refunds are available for thirty days.")

    result = service.map_claims("Refunds are available for thirty days.", [chunk])

    assert result.grounded is True
    assert result.claims[0]["evidence"]
    assert result.citation_coverage == 0.0


def test_markdown_scaffolding_is_not_treated_as_unsupported_claims() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))
    chunk = _chunk(content="Refunds are available for thirty days.")

    result = service.map_claims(
        "### Refund policy\n\n1. Refunds are available for thirty days. [1]\n\n|---|---:|",
        [chunk],
    )

    assert [claim["text"] for claim in result.claims] == [
        "Refunds are available for thirty days."
    ]
    assert result.grounded is True
    assert result.citation_coverage == 1.0


def test_trailing_citations_stay_with_each_sentence() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))
    chunks = [
        _chunk(content="Refunds are available for thirty days."),
        _chunk(content="Credentials rotate every ninety days."),
    ]

    result = service.map_claims(
        "Refunds are available for thirty days. [1] Credentials rotate every ninety days. [2]",
        chunks,
    )

    assert len(result.claims) == 2
    assert [claim["evidence"][0]["citation_index"] for claim in result.claims] == [1, 2]
    assert result.citation_coverage == 1.0


def test_valid_cross_vocabulary_citation_is_explicitly_unverified() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))
    chunk = _chunk(content="রিফান্ড ত্রিশ দিনের মধ্যে পাওয়া যায়।")

    result = service.map_claims("Refunds are available for thirty days. [1]", [chunk])

    assert result.grounded is True
    assert result.claims[0]["verification"] == "unverified"
    assert result.unverified_claim_rate == 1.0
    assert result.citation_coverage == 1.0


def test_zero_overlap_without_a_valid_citation_is_unsupported() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))

    result = service.map_claims(
        "Refunds are available for thirty days.",
        [_chunk(content="রিফান্ড ত্রিশ দিনের মধ্যে পাওয়া যায়।")],
    )

    assert result.grounded is False
    assert result.claims[0]["verification"] == "unsupported"
    assert result.unverified_claim_rate == 0.0


def test_partial_but_insufficient_overlap_is_unsupported() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.75))

    result = service.map_claims(
        "Refunds require manager approval and identity verification. [1]",
        [_chunk(content="Refunds are available for thirty days.")],
    )

    assert result.grounded is False
    assert result.claims[0]["verification"] == "unsupported"
