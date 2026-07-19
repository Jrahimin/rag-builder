"""Small deterministic quality gate that never calls external AI providers."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.core.config import ChatConfig, RetrievalConfig
from app.modules.conversations.grounding_service import GroundingService
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.schemas.message import InsufficientEvidenceReason
from app.modules.evaluation.schemas.evaluation import EvaluationDatasetCreate
from app.modules.retrieval.schemas.search import RetrievalResult
from app.modules.retrieval.services.duplicate_suppression_service import (
    DuplicateSuppressionService,
)
from app.platform.domain.text_tokenization import tokenize

pytestmark = pytest.mark.evaluation_smoke


def _result(
    *,
    chunk_id: str,
    document_id: str,
    project_id: str,
    content: str,
    source: str,
    chunk_index: int = 0,
) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=uuid.UUID(chunk_id),
        document_id=uuid.UUID(document_id),
        chunk_index=chunk_index,
        content=content,
        score=1.0,
        filename="policy.txt",
        metadata={"project": project_id, "source": source},
    )


def _lexical_search(
    query: str,
    corpus: list[RetrievalResult],
    *,
    project_id: str,
    metadata: dict[str, str] | None = None,
) -> list[RetrievalResult]:
    query_tokens = set(tokenize(query, for_query=True))
    filtered = [
        item
        for item in corpus
        if item.metadata["project"] == project_id
        and all(str(item.metadata.get(key)) == value for key, value in (metadata or {}).items())
    ]
    return sorted(
        [item for item in filtered if query_tokens & set(tokenize(item.content, for_query=True))],
        key=lambda item: (
            -len(query_tokens & set(tokenize(item.content, for_query=True))),
            item.chunk_index,
        ),
    )


def test_ci_fixture_declares_every_required_smoke_case() -> None:
    fixture = Path("tests/fixtures/evaluation/ci_smoke_v1.json")
    dataset = EvaluationDatasetCreate.model_validate(
        json.loads(fixture.read_text(encoding="utf-8"))
    )

    assert {case.key for case in dataset.cases} == {
        "direct-factual-retrieval",
        "metadata-filtering",
        "missing-evidence-refusal",
        "citation-coverage",
        "duplicate-chunks",
        "project-isolation",
        "bengali-query",
    }


def test_provider_free_retrieval_grounding_and_isolation_smoke() -> None:
    alpha = "alpha"
    beta = "beta"
    refund_chunk = "10000000-0000-0000-0000-000000000011"
    security_chunk = "10000000-0000-0000-0000-000000000012"
    alpha_document = "20000000-0000-0000-0000-000000000011"
    corpus = [
        _result(
            chunk_id=refund_chunk,
            document_id=alpha_document,
            project_id=alpha,
            content="The refund window is thirty days.",
            source="policy",
        ),
        _result(
            chunk_id="10000000-0000-0000-0000-000000000015",
            document_id=alpha_document,
            project_id=alpha,
            content="The refund window is thirty days.",
            source="policy",
            chunk_index=1,
        ),
        _result(
            chunk_id=security_chunk,
            document_id=alpha_document,
            project_id=alpha,
            content="Credentials rotate every ninety days.",
            source="security",
            chunk_index=2,
        ),
        _result(
            chunk_id="10000000-0000-0000-0000-000000000013",
            document_id="20000000-0000-0000-0000-000000000013",
            project_id=beta,
            content="Private project secret beta-only.",
            source="private",
        ),
        _result(
            chunk_id="10000000-0000-0000-0000-000000000014",
            document_id="20000000-0000-0000-0000-000000000014",
            project_id=alpha,
            content="ফেরতের সময়সীমা ত্রিশ দিন।",
            source="policy",
            chunk_index=3,
        ),
    ]

    direct = _lexical_search("refund window thirty", corpus, project_id=alpha)
    assert direct[0].chunk_id == uuid.UUID(refund_chunk)
    filtered = _lexical_search(
        "credentials rotate",
        corpus,
        project_id=alpha,
        metadata={"source": "security"},
    )
    assert [item.chunk_id for item in filtered] == [uuid.UUID(security_chunk)]
    assert _lexical_search("private project secret", corpus, project_id=alpha) == []
    assert _lexical_search("ফেরতের সময়সীমা", corpus, project_id=alpha)[0].content.endswith("দিন।")

    suppression = DuplicateSuppressionService(RetrievalConfig()).select(direct, limit=5)
    assert [item.chunk_id for item in suppression.results] == [uuid.UUID(refund_chunk)]
    assert suppression.suppressed_by_reason == {"content_hash": 1}

    grounding = GroundingService(ChatConfig(minimum_claim_token_coverage=0.3))
    refusal = grounding.assess("What is the lunar payroll rule?", [])
    assert refusal.reason is InsufficientEvidenceReason.NO_RETRIEVAL_RESULTS
    context = ContextChunk(
        chunk_id=uuid.UUID(security_chunk),
        document_id=uuid.UUID(alpha_document),
        chunk_index=2,
        content="Credentials rotate every ninety days.",
        score=1.0,
        filename="policy.txt",
        chunk_hash="deterministic",
    )
    claims = grounding.map_claims("Credentials rotate every ninety days. [1]", [context])
    assert claims.grounded is True
    assert claims.citation_coverage == 1.0
