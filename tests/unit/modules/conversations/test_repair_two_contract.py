"""Paired final-round regressions for arithmetic, temporal and structural contracts."""

import hashlib
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import ChatConfig
from app.models.index_build import IndexBuild
from app.modules.conversations.grounding_service import GroundingService
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.services.claim_entailment_service import ClaimEntailmentService
from app.modules.conversations.turn_resolution import normalize_request_scope
from app.modules.retrieval.structural_contract import (
    validate_structural_manifest,
    verify_structural_build,
)
from app.platform.domain.content_hash import content_hash
from app.platform.jobs.errors import PermanentJobError
from app.platform.providers.contracts.llm import ChatCompletionResult, ChatUsage
from app.platform.providers.implementations.openai_chat import OpenAIChatProvider


def source(text):
    return ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        filename="proof.txt",
        content=text,
        score=1,
        chunk_index=0,
        chunk_hash=content_hash(text),
        metadata={
            "reviewed_proof": [
                {
                    "requirement_id": "R1",
                    "quote": text,
                    "fulfillment": "full",
                    "supported_scope": text,
                }
            ]
        },
    )


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "question,inclusive",
    [
        ("What was known before 2025-01-01?", False),
        ("What information was available on 2025-01-01?", True),
    ],
)
def test_known_date_boundary_is_explicit(question, inclusive):
    scope = normalize_request_scope(question)
    assert scope.temporal_basis == "known_at"
    assert scope.exact_as_of == datetime(2025, 1, 1, tzinfo=UTC)
    assert scope.known_at_inclusive is inclusive


def test_instrument_title_year_is_not_applicability_period():
    title = normalize_request_scope("Under the Companies Act 1994, when is the first AGM?")
    assert not title.requested_periods
    assert title.exact_as_of is None
    assert normalize_request_scope("Threshold for AY 1994").requested_periods[0].start_year == 1994
    assert normalize_request_scope("Threshold for FY 2025-26").requested_periods[0].kind == "fiscal"


async def test_arithmetic_requires_semantic_scope_and_scenario_operand():
    class Entailment:
        async def verify(self, assertions):
            return [
                "supported"
                if item["assertion"] == "For tenants in AY 2025, 1000 \u00d7 10% = 100."
                else "unsupported"
                for item in assertions
            ]

    evidence = source("For tenants in AY 2025, the rate is 10%.")
    grounding = GroundingService(ChatConfig(), entailment=Entailment())
    positive = await grounding.map_claims(
        "For tenants in AY 2025, 1000 \u00d7 10% = 100. [1]",
        [evidence],
        user_input="Tenant AY 2025 base BDT 1000",
    )
    assert positive.claims[0]["verification"] == "supported"
    for assertion in [
        "For owners in AY 2026, 1000 \u00d7 10% = 100.",
        "Owners must submit a fire safety certificate; 1000 \u00d7 10% = 100.",
    ]:
        result = await grounding.map_claims(
            assertion + " [1]", [evidence], user_input="Tenant AY 2025 base BDT 1000"
        )
        assert all(claim["verification"] != "supported" for claim in result.claims)
    unauthorized = await grounding.map_claims(
        "For tenants in AY 2025, 1000 \u00d7 10% = 100. [1]",
        [evidence],
        user_input="Tenant AY 2025 base BDT 2000",
    )
    assert unauthorized.claims[0]["verification"] != "supported"


async def test_internal_entailment_uses_configured_openai_parameter_contract():
    provider = OpenAIChatProvider(api_key="fixture", model="gpt-6-luna", provider_version="1")

    async def generate(messages, *, temperature, max_tokens, **kwargs):
        body = provider._body(
            messages, temperature=temperature, max_tokens=max_tokens, stream=False
        )
        assert "temperature" not in body
        return ChatCompletionResult(
            content='{"verdicts":["supported"]}',
            provider="openai",
            model="gpt-6-luna",
            provider_version="1",
            finish_reason="stop",
            usage=ChatUsage(1, 1),
        )

    provider.generate = generate
    assert await ClaimEntailmentService(provider).verify(
        [{"assertion": "Tenant", "proof": [{"quote": "Tenant"}]}]
    ) == ["supported"]
    with pytest.raises(Exception, match="temperature"):
        provider._body([], temperature=0, max_tokens=100, stream=False)


def test_structural_intent_rejects_ordinary_manifest_but_preserves_ordinary_build():
    ordinary = IndexBuild(
        id=uuid.uuid4(), manifest={"documents": []}, structural_contract_version=None
    )
    validate_structural_manifest(ordinary)
    ordinary.structural_contract_version = "structure.v1"
    with pytest.raises(PermanentJobError):
        validate_structural_manifest(ordinary)
    ordinary.manifest = {"documents": [{"chunk_generation_id": str(ordinary.id)}]}
    validate_structural_manifest(ordinary)
    ordinary.manifest["documents"][0]["chunk_generation_id"] = str(uuid.uuid4())
    with pytest.raises(PermanentJobError):
        validate_structural_manifest(ordinary)


async def test_structural_replay_verifies_final_text_and_vector_identity():
    project, document, build_id, chunk_id = (uuid.uuid4() for _ in range(4))
    text = "Tenants supply a lease."
    chunk = SimpleNamespace(
        id=chunk_id,
        document_id=document,
        document_version=2,
        generation_id=build_id,
        content=text,
        chunk_metadata={
            "structure_version": "structure.v1",
            "structural_unit_id": hashlib.sha256(text.encode()).hexdigest(),
            "source_spans": [{"text": text}],
        },
    )
    vector = SimpleNamespace(
        chunk_id=chunk_id,
        input_content_hash=content_hash(text),
        document_version=2,
        embedding_schema_version=1,
        provider="cohere",
        model="embed-v4.0",
        dimensions=1024,
        embedding_set_version=3,
    )
    build = IndexBuild(
        id=build_id,
        project_id=project,
        structural_contract_version="structure.v1",
        chunk_count=1,
        manifest={
            "documents": [
                {
                    "document_id": str(document),
                    "document_version": 2,
                    "chunk_count": 1,
                    "chunk_generation_id": str(build_id),
                }
            ],
            "embedding_provider": "cohere",
            "embedding_model": "embed-v4.0",
            "embedding_dimensions": 1024,
            "embedding_set_version": 3,
        },
    )

    async def verify():
        session = AsyncMock()
        session.execute.side_effect = [
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [chunk])),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [vector])),
        ]
        await verify_structural_build(session, project, build)

    await verify()
    vector.input_content_hash = "foreign"
    with pytest.raises(PermanentJobError):
        await verify()
    vector.input_content_hash = content_hash(text)
    chunk.chunk_metadata["structural_unit_id"] = "stale"
    with pytest.raises(PermanentJobError):
        await verify()


def test_recovery_completion_is_counted_without_proven_requirements():
    from time import monotonic

    from app.modules.conversations.services.recovery_schedule import RecoverySchedule

    schedule = RecoverySchedule(monotonic() + 20)
    assert schedule.admit(
        "delta_review",
        requirement_ids=["missing"],
        fingerprint="no-proof",
        expected_change="review new rows",
    )
    schedule.complete("delta_review", changed=False)
    assert not schedule.admit(
        "delta_review",
        requirement_ids=["missing"],
        fingerprint="late-proof",
        expected_change="review again",
    )
    assert schedule.counts["delta_review"] == 1
    assert schedule.actions[0]["terminal_result"] == "no_progress"
    assert schedule.actions[0]["requirement_ids"] == ["missing"]
