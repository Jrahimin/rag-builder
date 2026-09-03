"""Focused checks that evaluation uses the production answer contract."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.composition.evaluation import GroundedEvaluationAnswerAdapter
from app.core.config import Settings
from app.modules.conversations.grounding_service import GroundingResult
from app.modules.evaluation.ports import QualityHit
from app.platform.providers.implementations.echo_chat import EchoLLMProvider

pytestmark = pytest.mark.unit


async def test_evaluation_claim_mapping_requires_the_same_citations_as_chat() -> None:
    adapter = GroundedEvaluationAnswerAdapter(
        settings=Settings(),
        llm=EchoLLMProvider(model="test", provider_version="1"),
    )
    adapter._grounding.map_claims = AsyncMock(  # type: ignore[method-assign]
        return_value=GroundingResult(
            claims=[],
            grounded=False,
            citation_coverage=0.0,
        )
    )
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
    assert adapter._grounding.map_claims.await_args.kwargs == {"require_citations": True}
