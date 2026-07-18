"""Unit tests for evidence sufficiency and claim-to-source mapping."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import ChatConfig
from app.modules.conversations.grounding_service import GroundingService
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.schemas.message import InsufficientEvidenceReason

pytestmark = pytest.mark.unit


def _chunk(*, content: str, score: float = 0.9) -> ContextChunk:
    return ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=2,
        content=content,
        score=score,
        filename="policy.pdf",
        chunk_hash="hash",
        page_number=4,
        char_start=120,
        char_end=240,
    )


def test_no_results_is_an_explicit_insufficient_evidence_outcome() -> None:
    decision = GroundingService(ChatConfig()).assess("unsupported question", [])
    assert decision.sufficient is False
    assert decision.reason is InsufficientEvidenceReason.NO_RETRIEVAL_RESULTS


def test_low_query_coverage_declines_unrelated_evidence() -> None:
    service = GroundingService(
        ChatConfig(minimum_query_token_coverage=0.5, minimum_evidence_score=0.01)
    )
    decision = service.assess(
        "What is the lunar payroll rule?",
        [_chunk(content="Customer refunds are available for thirty days.", score=0.2)],
    )
    assert decision.sufficient is False
    assert decision.reason is InsufficientEvidenceReason.LOW_QUERY_EVIDENCE_COVERAGE


def test_claims_link_to_cited_page_and_offsets() -> None:
    service = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))
    chunk = _chunk(content="Customer refunds are available for thirty days.")
    result = service.map_claims("Refunds are available for thirty days. [1]", [chunk])
    assert result.grounded is True
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
