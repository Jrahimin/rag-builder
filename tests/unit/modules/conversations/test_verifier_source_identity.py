"""The verifier receives the same source attribution as answer generation."""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.core.config import ChatConfig
from app.modules.conversations.grounding_service import GroundingService
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.services.claim_entailment_service import proof_binding_hash

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("reviewed", [False, True])
async def test_source_attribution_is_bound_without_granting_semantic_support(reviewed):
    text = "For assessment year 2026-2027, the zero-rate band is Tk 400,000."
    identity = uuid.uuid4()
    metadata = {"source_revision_id": str(uuid.uuid4())}
    if reviewed:
        metadata["reviewed_proof"] = [
            {"requirement_id": "R2", "quote": text, "supported_scope": text}
        ]
    source = ContextChunk(
        chunk_id=identity,
        document_id=uuid.uuid4(),
        chunk_index=1,
        filename="Finance Act 2026 Bangla.pdf",
        page_number=140,
        content=text,
        chunk_hash="source-hash",
        score=1,
        metadata=metadata,
    )
    verifier = AsyncMock()
    verifier.verify.return_value = ["unsupported"]
    verifier.last_verdicts = []
    claim = "The Finance Act 2026 indexed edition states a zero-rate band of Tk 400,000."
    result = await GroundingService(ChatConfig(), entailment=verifier).map_claims(
        claim,
        [source],
        draft_segments=[
            {
                "assertion_id": "tax-limit",
                "text": claim,
                "requirement_ids": ["R2"],
                "proof_ids": [str(identity)],
            }
        ],
        canonical_scope={"requested_periods": [{"start_year": 2026, "end_year": 2027}]},
    )
    proof = verifier.verify.call_args.args[0][0]["proof"][0]
    assert proof["quote"] == text
    assert proof["source_identity"] == {
        "document_id": str(source.document_id),
        "filename": source.filename,
        "page_number": 140,
        "source_revision_id": metadata["source_revision_id"],
    }
    changed = {**proof, "source_identity": {**proof["source_identity"], "filename": "Other.pdf"}}
    assert proof_binding_hash(changed) != proof_binding_hash(proof)
    assert result.claims[0]["verification"] == "unsupported"
    assert not result.grounded
