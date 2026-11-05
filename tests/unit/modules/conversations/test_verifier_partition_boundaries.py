"""Capacity and partial-failure boundaries of the bound verifier protocol."""

import asyncio
import json
import time
import uuid
from unittest.mock import AsyncMock

import pytest

from app.modules.conversations.services.claim_entailment_service import ClaimEntailmentService
from app.platform.providers.contracts.llm import ChatCompletionResult, ChatUsage
from app.platform.providers.errors import ProviderError, ProviderTimeoutError
from app.platform.providers.request_work import RequestWork

pytestmark = pytest.mark.unit


def assertions(count, proofs):
    return [
        {
            "assertion_id": f"claim-{index}",
            "assertion": "A complete independent rule.",
            "proof": [
                {"quote": "A complete independent rule.", "chunk_id": str(uuid.uuid4())}
                for _ in range(proofs)
            ],
            "canonical_scope": {
                "exact_as_of": "2025-07-01T00:00:00Z",
                "temporal_basis": "known_at",
            },
        }
        for index in range(count)
    ]


def provider(mutation=None):
    llm = AsyncMock()
    llm.supports_output_contract = True
    calls = []

    async def generate(messages, **kwargs):
        items = json.loads(messages[1].content)
        calls.append(
            {"ids": [item["assertion_id"] for item in items], "max_tokens": kwargs["max_tokens"]}
        )
        if len(calls) == 2:
            if mutation == "unavailable":
                raise ProviderError("offline failure", provider_name="fixture")
            if mutation == "timeout":
                raise ProviderTimeoutError("offline deadline", provider_name="fixture")
            if mutation == "phase_timeout":
                await asyncio.sleep(0.2)
        rows = [
            {
                "assertion_id": item["assertion_id"],
                "proof_hashes": item["proof_hashes"],
                "scope_hash": item["scope_hash"],
                "status": "supported",
                "failed_dimensions": [],
                "evidence_binding": "exact source identity",
                "publication_complete": True,
            }
            for item in items
        ]
        if len(calls) == 2 and mutation == "missing":
            rows.pop()
        if len(calls) == 2 and mutation == "foreign_proof":
            rows[0]["proof_hashes"] = ["f" * 64]
        if len(calls) == 2 and mutation == "foreign_scope":
            rows[0]["scope_hash"] = "f" * 64
        if len(calls) == 2 and mutation == "foreign_partition":
            rows[0]["assertion_id"] = calls[0]["ids"][0]
        if len(calls) == 2 and mutation == "duplicate":
            rows.append(dict(rows[0]))
        if len(calls) == 2 and mutation == "proof_order":
            rows[0]["proof_hashes"].reverse()
        rows.reverse()
        body = json.dumps(
            {"version": "claim.entailment.v2", "verdicts": rows}, separators=(",", ":")
        )
        # Portable conservative cap: tokens cannot exceed UTF-8 bytes. No vocabulary download.
        truncated = len(body.encode()) > kwargs["max_tokens"]
        return ChatCompletionResult(
            content=body[:20] if truncated else body,
            provider="fixture",
            model="fixture",
            provider_version="1",
            finish_reason="length" if truncated else "stop",
            usage=ChatUsage(1, 1),
        )

    llm.generate.side_effect = generate
    return llm, calls


@pytest.mark.parametrize(("count", "proofs"), [(1, 1), (12, 2), (20, 1), (8, 6)])
async def test_room_for_large_identity_batches(count, proofs):
    llm, calls = provider()
    service = ClaimEntailmentService(llm)
    rows = assertions(count, proofs)
    assert await service.verify(rows) == ["supported"] * count
    assert [row["assertion_id"] for row in service.last_verdicts] == [
        row["assertion_id"] for row in rows
    ]
    assert all(row["publication_complete"] is True for row in service.last_verdicts)
    assert sorted(identity for call in calls for identity in call["ids"]) == sorted(
        row["assertion_id"] for row in rows
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "unavailable",
        "timeout",
        "missing",
        "duplicate",
        "foreign_proof",
        "foreign_scope",
        "foreign_partition",
    ],
)
async def test_failed_partition_preserves_completed_bound_verdicts(mutation):
    llm, calls = provider(mutation)
    service = ClaimEntailmentService(llm)
    rows = assertions(20, 2)
    result = await service.verify(rows)
    assert len(calls) >= 2
    failed = set(calls[1]["ids"])
    assert all(result[int(identity.split("-")[1])] == "supported" for identity in calls[0]["ids"])
    assert all(result[int(identity.split("-")[1])] == "unverified" for identity in failed)
    assert len(service.last_verdicts) == len(rows)
    assert all(
        row["assertion_id"] == item["assertion_id"]
        for row, item in zip(service.last_verdicts, rows, strict=True)
    )
    assert all(
        row["publication_complete"] is None
        for row in service.last_verdicts
        if row["assertion_id"] in failed
    )
    assert service.last_failure is not None
    if mutation == "timeout":
        assert len(calls) == 2


async def test_no_partition_starts_after_absolute_cutoff(monkeypatch):
    work = RequestWork(uuid.uuid4())
    work.configure_execution("bounded_v2")
    clock = [work.started]
    monkeypatch.setattr(
        "app.modules.conversations.services.claim_entailment_service.current_request_work",
        lambda: work,
    )
    monkeypatch.setattr("time.perf_counter", lambda: clock[0])
    llm, calls = provider()
    original = llm.generate.side_effect

    async def generate(messages, **kwargs):
        result = await original(messages, **kwargs)
        clock[0] = work.phase_deadline("claim_verification") + 1
        return result

    llm.generate.side_effect = generate
    service = ClaimEntailmentService(llm)
    result = await service.verify(assertions(20, 2))
    assert len(calls) == 1
    completed = len(calls[0]["ids"])
    assert result[:completed] == ["supported"] * completed
    assert result[completed:] == ["unverified"] * (20 - completed)


async def test_phase_timeout_preserves_first_partition(monkeypatch):
    work = RequestWork(uuid.uuid4())
    work.configure_execution("bounded_v2")
    monkeypatch.setattr(
        "app.modules.conversations.services.claim_entailment_service.current_request_work",
        lambda: work,
    )
    monkeypatch.setattr(work, "phase_deadline", lambda purpose=None: time.perf_counter() + 0.02)
    llm, calls = provider("phase_timeout")
    service = ClaimEntailmentService(llm)
    result = await service.verify(assertions(20, 2))
    assert len(calls) == 2
    completed = len(calls[0]["ids"])
    assert result[:completed] == ["supported"] * completed
    assert result[completed:] == ["unverified"] * (20 - completed)


async def test_timeout_before_any_supported_partition_keeps_timeout_semantics():
    llm, _calls = provider()
    llm.generate.side_effect = ProviderTimeoutError("offline deadline", provider_name="fixture")
    service = ClaimEntailmentService(llm)
    with pytest.raises(ProviderTimeoutError):
        await service.verify(assertions(1, 1))
    assert service.last_failure["failure_category"] == "timeout"


async def test_complete_json_with_length_finish_is_never_salvaged():
    from dataclasses import replace

    llm, calls = provider()
    original = llm.generate.side_effect

    async def generate(messages, **kwargs):
        response = await original(messages, **kwargs)
        return replace(response, finish_reason="length")

    llm.generate.side_effect = generate
    service = ClaimEntailmentService(llm)
    assert await service.verify(assertions(1, 1)) == ["unverified"]
    assert len(calls) == 1
    assert service.last_failure["reason"] == "verifier_schema_invalid"


@pytest.mark.parametrize("mutation", ["unavailable", "timeout"])
async def test_grounding_keeps_publication_alignment_after_partition_failure(mutation):
    from app.core.config import ChatConfig
    from app.modules.conversations.grounding_service import GroundingService
    from tests.unit.modules.conversations.test_phase2_completion import source

    llm, calls = provider(mutation)
    verifier = ClaimEntailmentService(llm)
    grounding = GroundingService(ChatConfig(), entailment=verifier)
    chunks = [
        source(f"Record {index}: a receipt is required for this application.")
        for index in range(20)
    ]
    rows = [
        {
            "assertion_id": f"claim-{index}",
            "text": chunk.content,
            "requirement_ids": [f"R{index}"],
            "proof_ids": [str(chunk.chunk_id)],
        }
        for index, chunk in enumerate(chunks)
    ]
    result = await grounding.map_claims(
        "\n\n".join(chunk.content for chunk in chunks),
        chunks,
        draft_segments=rows,
        canonical_scope={"exact_as_of": "2025-07-01T00:00:00Z", "temporal_basis": "known_at"},
    )
    supported = set(calls[0]["ids"])
    failed = set(calls[1]["ids"])
    assert len(result.claims) == 20
    assert all(
        claim["verification"] == "supported"
        for claim in result.claims
        if claim["assertion_id"] in supported
    )
    assert all(
        claim["verification"] == "unverified"
        for claim in result.claims
        if claim["assertion_id"] in failed
    )
    assert all(grounding.publication_completeness[identity] is True for identity in supported)
    assert all(grounding.publication_completeness[identity] is None for identity in failed)
    assert all(
        claim["verification_reason"] == "verifier_unavailable"
        for claim in result.claims
        if claim["assertion_id"] in failed
    )


@pytest.mark.parametrize(
    "mutation", ["long_id", "excess_assertions", "excess_proofs", "duplicate_proofs"]
)
async def test_invalid_input_inventory_never_reaches_provider(mutation):
    llm, calls = provider()
    rows = assertions(1, 1)
    if mutation == "long_id":
        rows[0]["assertion_id"] = "x" * 81
    if mutation == "excess_assertions":
        rows = assertions(61, 1)
    if mutation == "excess_proofs":
        rows = assertions(1, 61)
    if mutation == "duplicate_proofs":
        rows[0]["proof"] *= 2
    service = ClaimEntailmentService(llm)
    assert await service.verify(rows) == ["unverified"] * len(rows)
    assert not calls
    assert service.last_failure["reason"] == "verifier_schema_invalid"


async def test_reordered_proof_hashes_remain_bound_to_the_same_assertion():
    llm, calls = provider("proof_order")
    service = ClaimEntailmentService(llm)
    rows = assertions(20, 2)
    assert await service.verify(rows) == ["supported"] * 20
    assert len(calls) > 1
    assert [row["assertion_id"] for row in service.last_verdicts] == [
        row["assertion_id"] for row in rows
    ]
