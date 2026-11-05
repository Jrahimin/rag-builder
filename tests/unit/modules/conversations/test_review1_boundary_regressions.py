"""Producer/consumer regressions for the independent review's six findings."""

import uuid
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import ChatConfig
from app.modules.conversations.calculation_graph import CalculationGraph
from app.modules.conversations.citation_snapshots import build_citation_snapshots
from app.modules.conversations.context_builder import ContextBuilder
from app.modules.conversations.current_authority import annotate_authority_limitations
from app.modules.conversations.grounded_context import reconstruct_reused_evidence
from app.modules.conversations.grounding_service import _evidence_unit, _SelectedSpan
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.services.evidence_coverage import CoverageVerdict
from app.modules.conversations.services.evidence_repair_service import (
    EvidenceRequirement,
    _guard_review_fulfillment,
)
from app.modules.conversations.services.message_execution_runner_service import (
    _calculation_operands,
)
from app.modules.retrieval.retrievers.models import CandidateHit, CandidateSource
from app.modules.retrieval.retrievers.result_hydrator import ResultHydrator
from app.modules.retrieval.services.search_service import SearchService
from tests.unit.modules.conversations.test_phase2_completion import execute_contract
from tests.unit.modules.conversations.test_post_qa_contract import journey

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("transport", ["regular", "sse", "get"])
@pytest.mark.parametrize("saved_notice", [False, True])
async def test_partial_verification_timeout_persists_checkpoint_through_chat_service(
    transport, saved_notice
):
    service, provider, conversation, repository, question = journey()
    service._chat_config = service._runner._chat_config = ChatConfig(
        execution_policy="bounded_v2", bounded_recovery_enabled=True
    )
    original = service._runner._verify_claims

    async def interrupted(prepared, content, chunks, **kwargs):
        result = await original(prepared, content, chunks, **kwargs)
        service._runner._checkpoint_claims(prepared, result.claims[:1])
        if saved_notice:
            checkpoint = service._runner._verified_checkpoint.restore(prepared.intent.scope)
            checkpoint.metadata["notices"] = [
                {"kind": "fixture", "text": "Retained notice", "language": "en"}
            ]
            service._runner._verified_checkpoint = type(
                service._runner._verified_checkpoint
            ).capture(checkpoint)
        raise TimeoutError("interrupted after first verified claim")

    service._runner._verify_claims = interrupted
    response = await execute_contract(
        service, provider, conversation, repository, question, transport
    )
    saved = [
        call.args[0]
        for call in repository.add.call_args_list
        if getattr(call.args[0], "role", None) == "assistant"
    ][-1]
    assert response["terminal_outcome"]["outcome"] == "partial"
    assert response["grounded"] is True and len(response["claims"]) == 1
    assert "18 months" in response["content"] and "15 months" not in response["content"]
    assert response["claims"] == saved.claims
    assert response["citations"] and len(response["citations"]) == len(saved.citations)
    assert saved.message_metadata["evidence_summary"] == {
        "coverage": "partial",
        "claim_verification": "supported",
    }
    assert saved.message_metadata["verified_checkpoint"]
    assert saved.message_metadata["execution"]["terminal"]["outcome"] == "partial"
    if saved_notice:
        assert saved.message_metadata["notices"][0]["text"] == "Retained notice"


async def hydrated_contexts():
    project, document, build, revision = (uuid.uuid4() for _ in range(4))
    rows = [
        SimpleNamespace(
            id=uuid.uuid4(),
            document_id=document,
            chunk_index=i,
            content=text,
            chunk_metadata={"source_revision_id": str(revision)},
            page_number=1,
            char_start=0,
            char_end=len(text),
            document_version=1,
        )
        for i, text in enumerate(
            ["Tenant means an occupier under a lease.", "Tenants must submit a lease."]
        )
    ]
    session = AsyncMock()
    session.scalars.return_value = []
    hydrator = ResultHydrator(session, project)
    hydrator._chunk_repository.map_by_ids = AsyncMock(return_value={row.id: row for row in rows})
    hydrator._document_repository.map_by_ids = AsyncMock(
        return_value={document: SimpleNamespace(filename="rules.txt")}
    )
    hits = [
        CandidateHit(
            chunk_id=row.id,
            score=1.0,
            source=CandidateSource.SEMANTIC,
            metadata={"project_id": str(uuid.uuid4()), "source_metadata_generation": 999},
        )
        for row in rows
    ]
    hydrated = await hydrator.hydrate(hits, index_build_id=build, source_generation=0)
    return project, build, hydrated, [ContextChunk.from_retrieval_result(row) for row in hydrated]


@pytest.mark.parametrize(
    "drift", [None, "project", "build", "generation", "missing_project", "duplicate_project"]
)
async def test_hydration_identity_binds_cross_section_definition_dependency(drift):
    project, _build, _, contexts = await hydrated_contexts()
    assert all(
        c.project_id == project and c.metadata["source_metadata_generation"] == 0 for c in contexts
    )
    if drift == "project":
        contexts[1] = replace(contexts[1], project_id=uuid.uuid4())
    if drift == "missing_project":
        contexts[1] = replace(contexts[1], project_id=None)
    if drift in {"build", "generation"}:
        contexts[1] = replace(
            contexts[1],
            metadata={
                **contexts[1].metadata,
                "index_build_id" if drift == "build" else "source_metadata_generation": str(
                    uuid.uuid4()
                )
                if drift == "build"
                else 1,
            },
        )
    requirements = [
        EvidenceRequirement(requirement_id="D", description="Definition of tenant"),
        EvidenceRequirement(
            requirement_id="R",
            description="Tenants submit a lease",
            depends_on=["D"],
            dependency_purposes={"D": "definition"},
        ),
    ]
    review = CoverageVerdict.model_validate(
        {
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "requirement_id": identity,
                    "supported": True,
                    "fulfillment": "full",
                    "evidence": [{"chunk_id": str(chunk.chunk_id), "quote": chunk.content}],
                }
                for identity, chunk in zip(["D", "R"], contexts, strict=True)
            ],
        }
    )
    if drift == "duplicate_project":
        contexts.append(replace(contexts[1], project_id=uuid.uuid4()))
    guarded = _guard_review_fulfillment(review, requirements, contexts)
    assert guarded.complete is (drift is None)
    assert guarded.checks[0].supported is True


async def test_exact_recall_failed_discovery_invalidates_only_affected_authority_identity():
    project, build, hydrated, contexts = await hydrated_contexts()
    # Independent proof has no governed source identity; failure belongs to the base.
    contexts[1] = replace(
        contexts[1],
        metadata={k: v for k, v in contexts[1].metadata.items() if k != "source_revision_id"},
    )
    search = SearchService.__new__(SearchService)
    search._project_id, search._request_scope = project, {}
    search._source_metadata = SimpleNamespace(
        incoming_modifiers=AsyncMock(side_effect=RuntimeError("read unavailable"))
    )
    records, status, reasons = await search._identity_modifier_diagnostics(
        [hydrated[0]],
        source_scope=SimpleNamespace(generation=0, explicit_as_of=None),
        index_build_id=build,
        as_of=None,
    )
    assert status == "unavailable" and reasons == {"discovery_unavailable": 1}
    assert records[0]["base_revision_id"] == contexts[0].metadata["source_revision_id"]
    annotated = annotate_authority_limitations(contexts, records)
    assert annotated[0].metadata["authority_status"] == "unresolved"
    assert annotated[1].metadata.get("authority_status") != "unresolved"
    units = [
        _evidence_unit(
            c,
            _SelectedSpan(c.content, 0, len(c.content), "complete_chunk", 1.0, True),
            query_variant_id="original",
            corroboration_method="original_semantic",
        )
        for c in contexts
    ]
    citations = build_citation_snapshots(
        units,
        config=ChatConfig(),
        project_id=project,
        config_snapshot_id=None,
        config_provenance={},
        prompt_version="fixture",
    )
    affected, diagnostic = reconstruct_reused_evidence(
        chunks=contexts,
        citations=citations[:1],
        expansion_records=records,
        context_builder=ContextBuilder(ChatConfig()),
    )
    assert not affected and diagnostic["reuse_validation_outcome"] == "failed"
    independent, diagnostic = reconstruct_reused_evidence(
        chunks=contexts,
        citations=citations[1:],
        expansion_records=records,
        context_builder=ContextBuilder(ChatConfig()),
    )
    assert independent and diagnostic["reuse_validation_outcome"] == "passed"


@pytest.mark.parametrize("text", ["10 days and 2 months", "10 USD and 2 BDT"])
def test_operand_registry_rejects_mixed_concrete_units(text):
    operands = _calculation_operands(text, [])
    graph = CalculationGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "sum",
                    "operation": "addition",
                    "operands": ["input:q0", "input:q1"],
                    "result": 12,
                }
            ]
        }
    )
    valid, invalid = graph.component_expressions(operands["inputs"], {}, operands["bindings"])
    assert not valid and invalid == {"sum": "incompatible_units"}


@pytest.mark.parametrize("order", [["input:q0", "input:q1"], ["input:q1", "input:q0"]])
def test_rate_money_derivation_keeps_currency_for_later_addition(order):
    operands = _calculation_operands("10% of Tk 1000 plus Tk 100", [])
    graph = CalculationGraph.model_validate(
        {
            "nodes": [
                {"id": "tax", "operation": "multiplication", "operands": order, "result": 100},
                {
                    "id": "total",
                    "operation": "addition",
                    "operands": ["node:tax", "input:q2"],
                    "result": 200,
                },
            ]
        }
    )
    valid, invalid = graph.component_expressions(operands["inputs"], {}, operands["bindings"])
    assert set(valid) == {"tax", "total"} and not invalid
    assert operands["bindings"]["node:tax"]["unit"] == "BDT"


def test_unestablished_conversion_does_not_erase_independent_arithmetic():
    graph = CalculationGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "bad",
                    "operation": "multiplication",
                    "operands": ["input:a", "input:b"],
                    "result": 20,
                },
                {
                    "id": "dependent",
                    "operation": "addition",
                    "operands": ["node:bad", "input:c"],
                    "result": 21,
                },
                {
                    "id": "independent",
                    "operation": "addition",
                    "operands": ["input:c", "input:d"],
                    "result": 3,
                },
            ]
        }
    )
    values = {"a": Decimal(10), "b": Decimal(2), "c": Decimal(1), "d": Decimal(2)}
    bindings = {
        "input:" + k: {"unit": unit}
        for k, unit in zip(values, ["BDT", "month", "day", "day"], strict=True)
    }
    valid, invalid = graph.component_expressions(values, {}, bindings)
    assert set(valid) == {"independent"} and invalid == {
        "bad": "unestablished_derived_units",
        "dependent": "dependency_failed",
    }


async def test_reviewed_typed_draft_skips_locator_embeddings_but_keeps_bound_verifier():
    import json

    from app.modules.conversations.grounding_service import GroundingService
    from app.modules.conversations.services.claim_entailment_service import ClaimEntailmentService
    from app.modules.conversations.turn_resolution import normalize_request_scope
    from app.platform.providers.contracts.llm import ChatCompletionResult, ChatUsage
    from tests.unit.modules.conversations.test_grounding_service import _CountingEmbedder

    text = "The fee is Tk 100."
    chunk = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=text,
        filename="fee.txt",
        chunk_hash=None,
        score=1.0,
        metadata={
            "reviewed_proof": [{"requirement_id": "R", "quote": text, "fulfillment": "full"}]
        },
    )
    llm = AsyncMock()
    llm.supports_output_contract = True

    async def generate(messages, **kwargs):
        items = json.loads(messages[1].content)
        return ChatCompletionResult(
            content=json.dumps(
                {
                    "version": "claim.entailment.v2",
                    "verdicts": [
                        {
                            "assertion_id": item["assertion_id"],
                            "proof_hashes": item["proof_hashes"],
                            "scope_hash": item["scope_hash"],
                            "status": "supported",
                            "failed_dimensions": [],
                            "evidence_binding": "exact",
                            "publication_complete": True,
                        }
                        for item in items
                    ],
                }
            ),
            provider="fixture",
            model="fixture",
            provider_version="1",
            finish_reason="stop",
            usage=ChatUsage(1, 1),
        )

    llm.generate.side_effect = generate
    embedder = _CountingEmbedder({})
    result = await GroundingService(
        ChatConfig(), embedder=embedder, entailment=ClaimEntailmentService(llm)
    ).map_claims(
        text,
        [chunk],
        draft_segments=[
            {
                "assertion_id": "A1",
                "text": text,
                "requirement_ids": ["R"],
                "proof_ids": [str(chunk.chunk_id)],
            }
        ],
        canonical_scope=normalize_request_scope("What fee applies?").model_dump(mode="json"),
    )
    assert result.claims[0]["verification"] == "supported"
    assert embedder.calls == 0
    llm.generate.assert_awaited_once()
