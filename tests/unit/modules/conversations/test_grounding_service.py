"""Unit tests for evidence sufficiency and claim-to-source mapping."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import ChatConfig, EvidenceScoreMode
from app.modules.conversations.grounding_service import GroundingService
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.schemas.message import InsufficientEvidenceReason
from app.platform.providers.contracts.embedding import (
    BaseEmbeddingProvider,
    EmbeddingBatchResult,
    EmbeddingPurpose,
)

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


class _ClusterEmbeddingProvider(BaseEmbeddingProvider):
    """Map clustered texts to nearby vectors so cosine is high only inside a cluster."""

    def __init__(self, clusters: dict[str, str]) -> None:
        self._clusters = clusters

    @property
    def provider_name(self) -> str:
        return "test"

    @property
    def model_name(self) -> str:
        return "cluster"

    @property
    def dimensions(self) -> int:
        return 4

    @property
    def provider_version(self) -> str:
        return "1"

    async def embed_texts(
        self,
        texts: list[str],
        *,
        purpose: EmbeddingPurpose = EmbeddingPurpose.DOCUMENT,
    ) -> EmbeddingBatchResult:
        del purpose
        return EmbeddingBatchResult(
            vectors=[self._vector(text) for text in texts],
            provider=self.provider_name,
            model=self.model_name,
            dimensions=self.dimensions,
            provider_version=self.provider_version,
        )

    def _vector(self, text: str) -> list[float]:
        cluster = self._clusters.get(text, f"unique:{text}")
        known = {
            "table": [1.0, 0.0, 0.0, 0.0],
            "vacation": [0.0, 1.0, 0.0, 0.0],
            "refund": [0.0, 0.0, 1.0, 0.0],
        }
        return known.get(cluster, [0.0, 0.0, 0.0, 1.0])


def _cluster_embedder(clusters: dict[str, str]) -> _ClusterEmbeddingProvider:
    return _ClusterEmbeddingProvider(clusters)


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
    assert (
        config.cross_language_semantic_evidence_score_threshold
        < config.minimum_semantic_evidence_score
    )
    assert (
        config.cross_language_semantic_evidence_score_threshold
        == config.lexical_corroboration_floor_score
    )


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


def test_cross_language_semantic_threshold_cannot_exceed_semantic_bar() -> None:
    with pytest.raises(ValueError, match="cross_language_semantic_evidence_score_threshold"):
        ChatConfig(
            minimum_semantic_evidence_score=0.35,
            cross_language_semantic_evidence_score_threshold=0.4,
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


async def test_claims_link_to_cited_page_and_offsets() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))
    chunk = _chunk(content="Customer refunds are available for thirty days.")
    result = await service.map_claims("Refunds are available for thirty days. [1]", [chunk])
    assert result.grounded is True
    assert result.claims[0]["verification"] == "supported"
    assert result.citation_coverage == 1.0
    evidence = result.claims[0]["evidence"][0]
    assert evidence["chunk_id"] == str(chunk.chunk_id)
    assert evidence["page_number"] == 4
    assert evidence["char_start"] == 120
    assert evidence["char_end"] == 240


async def test_uncited_factual_claim_is_unsupported() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))
    chunk = _chunk(content="Customer refunds are available for thirty days.")

    result = await service.map_claims("Refunds are available for thirty days.", [chunk])

    assert result.grounded is False
    assert result.claims[0]["verification"] == "unsupported"
    assert result.citation_coverage == 0.0
    assert result.claims[0]["evidence"] == []


async def test_evaluation_claim_mapping_binds_retrieved_evidence_without_citations() -> None:
    chunk = _chunk(content="Cobalt escalation matrix assigns urgent incidents to Reliability.")
    service = GroundingService(ChatConfig())

    production = await service.map_claims("[echo] cobalt escalation matrix", [chunk])
    evaluation = await service.map_claims(
        "[echo] cobalt escalation matrix",
        [chunk],
        require_citations=False,
    )

    assert production.grounded is False
    assert production.citation_coverage == 0.0
    assert production.claims[0]["evidence"] == []
    assert evaluation.grounded is True
    assert evaluation.citation_coverage == 0.0
    assert evaluation.claims[0]["evidence"][0]["chunk_id"] == str(chunk.chunk_id)
    assert evaluation.claims[0]["verification"] == "supported"


async def test_markdown_scaffolding_is_not_treated_as_unsupported_claims() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))
    chunk = _chunk(content="Refunds are available for thirty days.")

    result = await service.map_claims(
        "### Refund policy\n\n1. Refunds are available for thirty days. [1]\n\n|---|---:|",
        [chunk],
    )

    assert [claim["text"] for claim in result.claims] == ["Refunds are available for thirty days."]
    assert result.grounded is True
    assert result.citation_coverage == 1.0


async def test_list_preamble_is_not_a_claim() -> None:
    table = "সঞ্চয়পত্র হইতে অর্জিত মুনাফা সম্পত্তির অধিগ্রহণ রপ্তানির বিপরীতে মোটরযান"
    claim = "Source tax categories include savings certificates and property acquisition."
    service = GroundingService(
        ChatConfig(minimum_claim_token_coverage=0.3),
        embedder=_cluster_embedder({claim: "table", table: "table"}),
    )

    result = await service.map_claims(
        f"The source tax deduction/collection areas are: [1]\n\n{claim} [1]",
        [_chunk(content=table)],
    )

    assert [item["text"] for item in result.claims] == [claim]
    assert result.claims[0]["verification"] == "supported"
    assert result.grounded is True


async def test_insufficiency_statement_is_not_an_unsupported_claim() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))
    chunk = _chunk(content="সঞ্চয়পত্র হইতে অর্জিত মুনাফা সম্পত্তির অধিগ্রহণ")

    result = await service.map_claims(
        "There is not enough indexed evidence to confirm a 15% company recipient rate. "
        "সঞ্চয়পত্র হইতে অর্জিত মুনাফা সম্পত্তির অধিগ্রহণ আছে। [1]",
        [chunk],
    )

    assert [item["verification"] for item in result.claims] == ["supported"]
    assert "not enough indexed evidence" not in result.claims[0]["text"].casefold()
    assert result.grounded is True


async def test_trailing_citations_stay_with_each_sentence() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))
    chunks = [
        _chunk(content="Refunds are available for thirty days."),
        _chunk(content="Credentials rotate every ninety days."),
    ]

    result = await service.map_claims(
        "Refunds are available for thirty days. [1] Credentials rotate every ninety days. [2]",
        chunks,
    )

    assert len(result.claims) == 2
    assert [claim["evidence"][0]["citation_index"] for claim in result.claims] == [1, 2]
    assert result.citation_coverage == 1.0


async def test_paragraph_final_citation_is_inherited_but_each_claim_is_verified() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))
    chunk = _chunk(
        content=(
            "The current investment rebate is 10% of eligible investment. "
            "For BDT 60,000 the rebate is BDT 6,000."
        )
    )
    result = await service.map_claims(
        "The current investment rebate is 10% of eligible investment. "
        "For BDT 60,000 the rebate is BDT 6,000. [1]",
        [chunk],
    )
    assert len(result.claims) == 2
    assert all(claim["verification"] == "supported" for claim in result.claims)
    assert result.citation_coverage == 1.0
    assert result.grounded is True


async def test_inherited_citation_does_not_ground_an_unrelated_preceding_claim() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))
    result = await service.map_claims(
        "Employees receive free housing. Refunds are available for thirty days. [1]",
        [_chunk(content="Customer refunds are available for thirty days.")],
    )
    assert [claim["verification"] for claim in result.claims] == ["unverified", "supported"]
    assert result.grounded is False


async def test_bounded_citations_support_a_displayed_calculation_block() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))
    result = await service.map_claims(
        "The investment rebate rate is 10%. [1]\n\n"
        "For BDT 60,000:\n\n"
        "**BDT 60,000 \u00d7 10% = BDT 6,000**\n\n"
        "Therefore, the rebate is BDT 6,000. [1]",
        [
            _chunk(
                content=(
                    "The investment rebate rate is 10%. For BDT 60,000, "
                    "the rebate is BDT 6,000."
                )
            )
        ],
    )

    calculation = next(claim for claim in result.claims if "6,000**" in claim["text"])
    assert calculation["verification"] == "supported"
    assert calculation["evidence"][0]["citation_index"] == 1
    assert result.grounded is True


async def test_bounded_citations_do_not_cross_an_uncited_factual_block() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))
    result = await service.map_claims(
        "The investment rebate rate is 10%. [1]\n\n"
        "**BDT 60,000 \u00d7 10% = BDT 6,000**\n\n"
        "Employees receive free housing.\n\n"
        "Therefore, the rebate is BDT 6,000. [1]",
        [
            _chunk(
                content=(
                    "The investment rebate rate is 10%. For BDT 60,000, "
                    "the rebate is BDT 6,000."
                )
            )
        ],
    )

    calculation = next(claim for claim in result.claims if "6,000**" in claim["text"])
    assert calculation["verification"] == "unsupported"
    assert result.grounded is False


async def test_short_meta_stance_does_not_make_a_grounded_correction_fail() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))
    result = await service.map_claims(
        "The claim is incorrect. The current investment rebate is 10%. [1]",
        [_chunk(content="The current investment rebate is 10%.")],
    )
    assert [claim["text"] for claim in result.claims] == ["The current investment rebate is 10%."]
    assert result.grounded is True


async def test_valid_citation_without_semantic_support_is_unverified() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))
    chunk = _chunk(content="রিফান্ড ত্রিশ দিনের মধ্যে পাওয়া যায়।")

    result = await service.map_claims("Refunds are available for thirty days. [1]", [chunk])

    assert result.grounded is False
    assert result.claims[0]["verification"] == "unverified"
    assert result.claims[0]["grounded"] is False
    assert result.unverified_claim_rate == 1.0
    assert result.citation_coverage == 1.0


async def test_bn_claim_to_bn_evidence_is_supported() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))
    chunk = _chunk(content="সঞ্চয়পত্র হইতে অর্জিত মুনাফা সম্পত্তির অধিগ্রহণ রপ্তানির বিপরীতে মোটরযান")

    result = await service.map_claims(
        "উৎসে কর খাতের মধ্যে সঞ্চয়পত্র, সম্পত্তির অধিগ্রহণ, রপ্তানি ও মোটরযান আছে। [1]",
        [chunk],
    )

    assert result.claims[0]["verification"] == "supported"
    assert result.grounded is True


async def test_en_claim_to_matching_bn_evidence_is_supported() -> None:
    table = "সঞ্চয়পত্র হইতে অর্জিত মুনাফা সম্পত্তির অধিগ্রহণ রপ্তানির বিপরীতে মোটরযান"
    claim = (
        "Source tax categories include savings certificates, property acquisition, "
        "exports, and vehicles."
    )
    service = GroundingService(
        ChatConfig(minimum_claim_token_coverage=0.3),
        embedder=_cluster_embedder({claim: "table", table: "table"}),
    )

    result = await service.map_claims(f"{claim} [1]", [_chunk(content=table)])

    assert result.claims[0]["verification"] == "supported"
    assert result.grounded is True


async def test_en_unrelated_claim_to_bn_evidence_is_unsupported() -> None:
    table = "সঞ্চয়পত্র হইতে অর্জিত মুনাফা সম্পত্তির অধিগ্রহণ রপ্তানির বিপরীতে মোটরযান"
    claim = "Employees receive twenty days of paid vacation each year."
    service = GroundingService(
        ChatConfig(minimum_claim_token_coverage=0.3),
        embedder=_cluster_embedder({claim: "vacation", table: "table"}),
    )

    result = await service.map_claims(f"{claim} [1]", [_chunk(content=table)])

    assert result.claims[0]["verification"] == "unsupported"
    assert result.grounded is False


async def test_zero_overlap_without_a_valid_citation_is_unsupported() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))

    result = await service.map_claims(
        "Refunds are available for thirty days.",
        [_chunk(content="রিফান্ড ত্রিশ দিনের মধ্যে পাওয়া যায়।")],
    )

    assert result.grounded is False
    assert result.claims[0]["verification"] == "unsupported"
    assert result.unverified_claim_rate == 0.0


async def test_partial_but_insufficient_overlap_is_unsupported() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.75))

    result = await service.map_claims(
        "Refunds require manager approval and identity verification. [1]",
        [_chunk(content="Refunds are available for thirty days.")],
    )

    assert result.grounded is False
    assert result.claims[0]["verification"] == "unsupported"


def test_applied_reranker_score_is_the_only_learned_evidence_path() -> None:
    from dataclasses import replace

    weak_cosine = replace(
        _chunk(content="nearby VAT chapter", semantic_score=0.9),
        rerank_relevance_score=0.11,
        metadata={"rerank_status": "applied"},
    )
    relevant = replace(
        _chunk(content="উৎসে কর সংগ্রহের খাত সঞ্চয়পত্র", semantic_score=0.32),
        rerank_relevance_score=0.81,
        metadata={"rerank_status": "applied"},
    )
    decision = GroundingService(ChatConfig(minimum_reranker_evidence_score=0.4)).assess(
        "what are the source tax deduction areas?",
        [weak_cosine, relevant],
    )
    assert decision.sufficient is True
    assert decision.evidence_score_method == "reranker_relevance"
    assert decision.winning_chunk_id == relevant.chunk_id


def test_high_cosine_cannot_override_a_low_applied_reranker_score() -> None:
    from dataclasses import replace

    chunk = replace(
        _chunk(content="nearby VAT chapter", semantic_score=0.92),
        rerank_relevance_score=0.12,
        metadata={"rerank_status": "applied"},
    )
    decision = GroundingService(ChatConfig(minimum_reranker_evidence_score=0.4)).assess(
        "what are the source tax deduction areas?",
        [chunk],
    )
    assert decision.sufficient is False
    assert decision.evidence_score_method == "reranker_relevance"


def test_applied_rerank_status_uses_reranker_threshold_without_chunk_metadata() -> None:
    from dataclasses import replace

    chunk = replace(
        _chunk(content="nearby VAT chapter", semantic_score=0.92),
        rerank_relevance_score=0.12,
        metadata={},
    )
    decision = GroundingService(ChatConfig(minimum_reranker_evidence_score=0.4)).assess(
        "what are the source tax deduction areas?",
        [chunk],
        rerank_status="applied",
    )
    assert decision.sufficient is False
    assert decision.evidence_score_method == "reranker_relevance"
    assert decision.best_score == pytest.approx(0.12)


def test_evidence_winner_follows_reranked_order() -> None:
    from dataclasses import replace

    first = replace(
        _chunk(content="উৎসে কর সংগ্রহের খাত সঞ্চয়পত্র", semantic_score=0.32),
        rerank_relevance_score=0.88,
        metadata={"rerank_status": "applied"},
    )
    later = replace(
        _chunk(content="high cosine neighbor", semantic_score=0.97),
        rerank_relevance_score=0.41,
        metadata={"rerank_status": "applied"},
    )
    decision = GroundingService(ChatConfig(minimum_reranker_evidence_score=0.4)).assess(
        "what are the source tax deduction areas?",
        [first, later],
    )
    assert decision.sufficient is True
    assert decision.winning_chunk_id == first.chunk_id
    assert decision.evidence_score_method == "reranker_relevance"


def test_applied_rerank_recovers_score_when_dedicated_field_is_missing() -> None:
    from dataclasses import replace

    chunk = replace(
        _chunk(content="উৎসে কর সংগ্রহের খাত সঞ্চয়পত্র", semantic_score=0.32, score=0.8693157),
        rerank_relevance_score=None,
        metadata={"rerank_status": "applied"},
    )
    decision = GroundingService(ChatConfig(minimum_reranker_evidence_score=0.4)).assess(
        "উৎসে কর সংগ্রহের খাত কি?",
        [chunk],
        rerank_status="applied",
    )
    assert decision.sufficient is True
    assert decision.best_score == pytest.approx(0.8693157)
    assert decision.evidence_score_method == "reranker_relevance"
    assert decision.winning_chunk_id == chunk.chunk_id


def test_applied_rerank_without_any_score_does_not_report_zero() -> None:
    from dataclasses import replace

    chunk = replace(
        _chunk(content="nearby VAT chapter", semantic_score=0.32, score=0.0),
        rerank_relevance_score=None,
        metadata={},
    )
    decision = GroundingService(ChatConfig(minimum_reranker_evidence_score=0.4)).assess(
        "what are the source tax deduction areas?",
        [chunk],
        rerank_status="applied",
    )
    assert decision.sufficient is False
    assert decision.best_score is None
    assert decision.evidence_score_method == "reranker_relevance"


def test_applied_rerank_admits_lexical_overlap_when_semantic_score_is_absent() -> None:
    from dataclasses import replace

    chunk = replace(
        _chunk(
            content="Cobalt escalation matrix assigns urgent incidents to Reliability.",
            semantic_score=None,
            score=0.71,
        ),
        rerank_relevance_score=0.71,
        metadata={"rerank_status": "applied"},
    )
    decision = GroundingService(ChatConfig()).assess(
        "cobalt escalation matrix",
        [chunk],
        rerank_status="applied",
    )
    assert decision.sufficient is True
    assert decision.lexically_corroborated is True
    assert decision.winning_semantic_score is None
    assert decision.evidence_score_method == "reranker_relevance"


def test_rerank_unavailable_falls_back_to_cosine_and_lexical_rescue() -> None:
    from dataclasses import replace

    chunk = replace(
        _chunk(
            content="উৎসে কর সংগ্রহের খাত কি সঞ্চয়পত্র হইতে অর্জিত মুনাফা",
            semantic_score=0.34,
        ),
        metadata={"rerank_status": "unavailable"},
    )
    decision = GroundingService(ChatConfig()).assess("উৎসে কর সংগ্রহের খাত কি?", [chunk])
    assert decision.sufficient is True
    assert decision.lexically_corroborated is True
    assert decision.evidence_score_method == "whole_chunk_cosine"


def test_observe_mode_records_the_same_refusal_without_changing_assessment() -> None:
    chunk = _chunk(
        content="Employee vacation policy requires manager approval.",
        semantic_score=0.32,
        score=0.018,
    )
    question = "what are the source tax deduction areas?"
    enforce = GroundingService(ChatConfig())
    observe = GroundingService(ChatConfig(evidence_gate_mode="observe"))

    enforced = enforce.assess(question, [chunk])
    observed = observe.assess(question, [chunk])

    assert enforced.sufficient is False
    assert observed.sufficient is False
    assert observed.reason is InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD
    assert observed.best_score == pytest.approx(0.32)
    assert enforce.blocks_generation(enforced) is True
    assert observe.blocks_generation(observed) is False
    diagnostics = observe.diagnostics(
        observed,
        blocked_generation=False,
        generation_ran=True,
    )
    assert diagnostics["mode"] == "observe"
    assert diagnostics["sufficient"] is False
    assert diagnostics["generation_ran"] is True
    assert diagnostics["blocked_generation"] is False
    assert diagnostics["winning_semantic_score"] == pytest.approx(0.32)
    assert diagnostics["reranker_threshold"] == pytest.approx(0.4)


def test_observe_mode_still_blocks_when_nothing_was_retrieved() -> None:
    decision = GroundingService(ChatConfig(evidence_gate_mode="observe")).assess(
        "unsupported question",
        [],
    )
    assert decision.sufficient is False
    assert decision.reason is InsufficientEvidenceReason.NO_RETRIEVAL_RESULTS
    assert GroundingService(ChatConfig(evidence_gate_mode="observe")).blocks_generation(decision)


def test_rrf_rank_score_is_never_the_evidence_score() -> None:
    from dataclasses import replace

    chunk = replace(
        _chunk(content="nearby VAT chapter", semantic_score=0.32, score=0.91),
        rank_score=0.91,
    )
    decision = GroundingService(ChatConfig()).assess(
        "what are the source tax deduction areas?",
        [chunk],
    )
    assert decision.sufficient is False
    assert decision.best_score == pytest.approx(0.32)
    assert decision.winning_semantic_score == pytest.approx(0.32)
    assert decision.winning_rank_score == pytest.approx(0.91)


_GAZETTE_TABLE = (
    "সারণী আয়ের উৎস/উৎসে কর সংগ্রহের খাত সঞ্চয়পত্র হইতে অর্জিত মুনাফা "
    "সম্পত্তির অধিগ্রহণ রপ্তানির বিপরীতে প্রাপ্ত নগদ ভর্তুকি"
)
_RATE_ROW = "কোম্পানি প্রাপকের উৎসে করের হার পনেরো শতাংশ।"


def _reranked(
    content: str,
    *,
    rerank: float,
    semantic: float,
) -> ContextChunk:
    from dataclasses import replace

    return replace(
        _chunk(content=content, semantic_score=semantic, score=rerank),
        rerank_relevance_score=rerank,
        metadata={"rerank_status": "applied"},
    )


@pytest.mark.parametrize(
    ("question", "content", "semantic"),
    [
        ("উৎসে কর সংগ্রহের খাত কি?", _GAZETTE_TABLE, 0.323),
        ("what are the source tax deduction areas?", _GAZETTE_TABLE, 0.323),
        ("উৎসে kor collection khat ki?", _GAZETTE_TABLE, 0.323),
    ],
)
def test_applied_rerank_admits_positive_multilingual_cases(
    question: str,
    content: str,
    semantic: float,
) -> None:
    decision = GroundingService(ChatConfig()).assess(
        question,
        [_reranked(content, rerank=0.869, semantic=semantic)],
        rerank_status="applied",
    )
    assert decision.sufficient is True
    assert decision.evidence_score_method == "reranker_relevance"
    assert decision.best_score == pytest.approx(0.869)
    assert decision.winning_semantic_score == pytest.approx(semantic)


def test_applied_rerank_rejects_unrelated_maternity_leave() -> None:
    decision = GroundingService(ChatConfig()).assess(
        "What is the maternity leave policy?",
        [_reranked(_GAZETTE_TABLE, rerank=0.61, semantic=0.18)],
        rerank_status="applied",
    )
    assert decision.sufficient is False
    assert decision.reason is InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD
    assert decision.best_score == pytest.approx(0.61)
    assert decision.winning_semantic_score == pytest.approx(0.18)


def test_applied_rerank_admits_lexical_overlap_without_semantic_score() -> None:
    content = "Cobalt escalation matrix assigns urgent incidents to Reliability."
    chunk = _reranked(content, rerank=0.71, semantic=None)
    decision = GroundingService(ChatConfig()).assess(
        "cobalt escalation matrix",
        [chunk],
        rerank_status="applied",
    )
    assert decision.sufficient is True
    assert decision.lexically_corroborated is True
    assert decision.winning_semantic_score is None
    assert decision.best_score == pytest.approx(0.71)


def test_applied_rerank_still_rejects_unrelated_hits_without_semantic_score() -> None:
    chunk = _reranked(
        "Cobalt escalation matrix assigns urgent incidents to Reliability.",
        rerank=0.71,
        semantic=None,
    )
    decision = GroundingService(ChatConfig()).assess(
        "What is the lunar payroll rule?",
        [chunk],
        rerank_status="applied",
    )
    assert decision.sufficient is False
    assert decision.reason is InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD


def test_category_query_does_not_admit_rate_row_on_rerank_alone() -> None:
    decision = GroundingService(ChatConfig()).assess(
        "উৎসে কর সংগ্রহের খাত কি?",
        [_reranked(_RATE_ROW, rerank=0.72, semantic=0.19)],
        rerank_status="applied",
    )
    assert decision.sufficient is False
    assert decision.reason is InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD


def test_category_query_admits_gazette_table_over_rate_neighbor() -> None:
    rate = _reranked(_RATE_ROW, rerank=0.52, semantic=0.19)
    table = _reranked(_GAZETTE_TABLE, rerank=0.87, semantic=0.323)
    decision = GroundingService(ChatConfig()).assess(
        "what are the source tax deduction areas?",
        [rate, table],
        rerank_status="applied",
    )
    assert decision.sufficient is True
    assert decision.winning_chunk_id == table.chunk_id


async def test_english_paraphrase_of_cited_english_evidence_is_supported() -> None:
    evidence = "Customer refunds are available for thirty days after purchase."
    claim = "Buyers can get their money back within a month of buying."
    service = GroundingService(
        ChatConfig(minimum_claim_token_coverage=0.35),
        embedder=_cluster_embedder({claim: "refund", evidence: "refund"}),
    )

    result = await service.map_claims(f"{claim} [1]", [_chunk(content=evidence)])

    assert result.claims[0]["verification"] == "supported"
    assert result.grounded is True
    assert result.citation_coverage == 1.0
