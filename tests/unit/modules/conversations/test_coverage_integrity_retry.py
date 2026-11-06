"""Valid line bounds cannot hide integrity-invalid full coverage selectors."""

import json
import uuid
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.modules.conversations.services.evidence_coverage import CoverageVerdict
from app.modules.conversations.services.evidence_repair_service import _validated_completion
from app.platform.providers.contracts.llm import (
    ChatCompletionResult,
    ChatMessage,
    ChatRole,
    ChatUsage,
)
from app.platform.providers.request_work import RequestWork
from tests.unit.modules.conversations.test_evidence_repair_service import chunk

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("corrected", [True, False])
async def test_integrity_rejection_gets_one_complete_review_and_stays_fail_closed(corrected):
    clean = chunk("For AY 2026-2027, ordinary individuals have a Tk 400,000 zero-rate band.")
    damaged = chunk("Special categories)) have separate thresholds.")

    def completion(include_damaged):
        return ChatCompletionResult(
            content=json.dumps(
                {
                    "complete": True,
                    "missing": [],
                    "checks": [
                        {
                            "requirement_id": "R1",
                            "supported": True,
                            "fulfillment": "full",
                            "evidence": [
                                {
                                    "chunk_id": "E1" if source is clean else "E2",
                                    "start_line": 1,
                                    "end_line": 1,
                                }
                                for source in ([clean, damaged] if include_damaged else [clean])
                            ],
                        }
                    ],
                }
            ),
            provider="fake",
            model="test",
            provider_version="fixture",
            finish_reason="stop",
            usage=ChatUsage(1, 1),
        )

    llm = AsyncMock()
    llm.work = RequestWork(uuid.uuid4())
    deadline = llm.work.deadline
    llm.generate.side_effect = [completion(True), completion(not corrected)]
    request = _validated_completion(
        llm,
        [ChatMessage(ChatRole.USER, "Review the original requirement against both sources.")],
        schema=CoverageVerdict,
        max_tokens=1024,
        proof_context=[clean, damaged],
        source_ids={"E1": str(clean.chunk_id), "E2": str(damaged.chunk_id)},
        expected_requirement_ids={"R1"},
        require_fulfillment=True,
    )
    if corrected:
        response = await request
        verdict = CoverageVerdict.model_validate_json(response.content)
        assert verdict.resolve_source_ranges([clean, damaged])
        assert verdict.validates([], [clean, damaged], {"R1"})
        assert [item.chunk_id for item in verdict.checks[0].evidence] == [str(clean.chunk_id)]
    else:
        with pytest.raises(ValidationError, match="extraction integrity"):
            await request
    assert llm.generate.await_count == 2
    assert "Re-review the complete supplied evidence" in llm.generate.call_args.args[0][-1].content
    assert llm.work.counts["structured_response_retries"] == 1
    assert llm.work.deadline == deadline
    failures = llm.work.validation_retries[0]["source_integrity_failures"]
    assert {row["chunk_id"] for row in failures} == {str(damaged.chunk_id)}
    assert {row["source_id"] for row in failures} == {"E2"}
