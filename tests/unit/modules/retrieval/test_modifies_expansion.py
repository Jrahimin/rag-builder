"""Depth-one current-authority expansion and governance tests."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import ModifiesExpansionMode, RetrievalStrategy
from app.models.source_metadata import SourceLifecycleStatus
from app.modules.knowledge.source_metadata_read import _modifier_outcome
from app.modules.retrieval.retrievers.hybrid_retriever import (
    HybridRetriever,
    _bound_modifier_records,
    _expansion_diagnostics,
)
from app.modules.retrieval.retrievers.models import (
    CandidateHit,
    CandidateSource,
    RetrievalContext,
    RetrievalFilters,
)
from app.modules.retrieval.retrievers.semantic_retriever import SemanticRetrievalBatch
from app.modules.retrieval.source_policy import (
    ModifierExpansionOutcome,
    ModifierExpansionRecord,
)
from app.platform.providers.contracts.reranker import (
    RerankResponse,
    RerankResult,
    RerankScoreScale,
)

pytestmark = pytest.mark.unit


def _governed_row(project_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        relationship_project_id=project_id,
        modifier_project_id=project_id,
        base_project_id=project_id,
        modifier_revision_id=uuid.uuid4(),
        modifier_document_id=uuid.uuid4(),
        modifier_source_group_id=uuid.uuid4(),
        modifier_revision_number=2,
        modifier_revision_label="2026 amendment",
        modifier_title="Policy amendment",
        modifier_lifecycle_status=SourceLifecycleStatus.ACTIVE,
        modifier_effective_from=date(2026, 1, 1),
        modifier_effective_to=None,
    )


@pytest.mark.parametrize(
    ("mutation", "selected", "indexed", "expected"),
    [
        ({}, "same", True, "expanded"),
        ({"modifier_lifecycle_status": SourceLifecycleStatus.RETIRED}, "same", True, "inactive"),
        ({"modifier_effective_from": date(2027, 1, 1)}, "same", True, "outside_as_of"),
        ({"modifier_effective_from": date(2027, 1, 1)}, "missing", True, "outside_as_of"),
        ({}, "same", False, "not_in_active_index"),
        ({"modifier_project_id": uuid.UUID(int=0)}, "same", True, "cross_project_or_generation"),
        ({}, "different", True, "stale_or_replaced_revision"),
        ({"modifier_title": ""}, "same", True, "ungoverned_or_incomplete_metadata"),
        ({}, "missing", True, "ungoverned_or_incomplete_metadata"),
    ],
)
def test_modifier_governance_has_one_fail_closed_outcome(
    mutation: dict[str, object],
    selected: str,
    indexed: bool,
    expected: str,
) -> None:
    project_id = uuid.uuid4()
    row = _governed_row(project_id)
    for key, value in mutation.items():
        setattr(row, key, value)
    selected_revision = {
        "same": row.modifier_revision_id,
        "different": uuid.uuid4(),
        "missing": None,
    }[selected]

    assert (
        _modifier_outcome(
            row,
            project_id=project_id,
            selected_revision=selected_revision,
            indexed=indexed,
            reference_date=date(2026, 8, 31),
        )
        == expected
    )


def test_replaces_activation_excludes_stale_modifier_revision() -> None:
    project_id = uuid.uuid4()
    row = _governed_row(project_id)
    activated_successor = uuid.uuid4()

    assert (
        _modifier_outcome(
            row,
            project_id=project_id,
            selected_revision=activated_successor,
            indexed=True,
            reference_date=date(2026, 8, 31),
        )
        == ModifierExpansionOutcome.STALE_OR_REPLACED_REVISION.value
    )


def _record(
    *,
    base_revision_id: uuid.UUID,
    base_document_id: uuid.UUID,
    modifier_revision_id: uuid.UUID | None = None,
    modifier_document_id: uuid.UUID | None = None,
    outcome: ModifierExpansionOutcome = ModifierExpansionOutcome.EXPANDED,
    effective_from: str = "2026-01-01",
) -> ModifierExpansionRecord:
    return ModifierExpansionRecord(
        relationship_id=uuid.uuid4(),
        base_revision_id=base_revision_id,
        base_document_id=base_document_id,
        modifier_revision_id=modifier_revision_id or uuid.uuid4(),
        modifier_document_id=modifier_document_id or uuid.uuid4(),
        modifier_effective_from=effective_from,
        modifier_published_date="2026-01-01",
        modifier_revision_number=1,
        outcome=outcome,
    )


def test_depth_one_visited_sets_diagnose_cycle_duplicate_and_source_cap() -> None:
    base_revision_id = uuid.uuid4()
    base_document_id = uuid.uuid4()
    duplicate_document_id = uuid.uuid4()
    first = _record(
        base_revision_id=base_revision_id,
        base_document_id=base_document_id,
        modifier_document_id=duplicate_document_id,
        effective_from="2026-04-01",
    )
    duplicate = _record(
        base_revision_id=base_revision_id,
        base_document_id=base_document_id,
        modifier_document_id=duplicate_document_id,
        effective_from="2026-03-01",
    )
    cycle = _record(
        base_revision_id=base_revision_id,
        base_document_id=base_document_id,
        modifier_revision_id=base_revision_id,
        effective_from="2026-02-01",
    )
    capped = _record(
        base_revision_id=base_revision_id,
        base_document_id=base_document_id,
        effective_from="2026-01-01",
    )

    bounded = _bound_modifier_records(
        [capped, cycle, duplicate, first],
        base_revision_ids={base_revision_id},
        base_document_ids={base_document_id},
        max_related_sources=1,
    )

    assert [item.outcome for item in bounded] == [
        ModifierExpansionOutcome.EXPANDED,
        ModifierExpansionOutcome.DUPLICATE,
        ModifierExpansionOutcome.CYCLE,
        ModifierExpansionOutcome.SOURCE_CAP_EXCEEDED,
    ]


def test_modifier_already_in_original_recall_is_not_reported_as_cycle() -> None:
    base_revision_id = uuid.uuid4()
    base_document_id = uuid.uuid4()
    modifier_revision_id = uuid.uuid4()
    modifier_document_id = uuid.uuid4()
    record = _record(
        base_revision_id=base_revision_id,
        base_document_id=base_document_id,
        modifier_revision_id=modifier_revision_id,
        modifier_document_id=modifier_document_id,
    )

    bounded = _bound_modifier_records(
        [record],
        base_revision_ids={base_revision_id, modifier_revision_id},
        base_document_ids={base_document_id, modifier_document_id},
        max_related_sources=1,
    )

    assert bounded[0].outcome is ModifierExpansionOutcome.ALREADY_IN_RECALL


def test_unscoped_current_authority_relationship_is_explicit_in_diagnostics() -> None:
    record = _record(
        base_revision_id=uuid.uuid4(),
        base_document_id=uuid.uuid4(),
        outcome=ModifierExpansionOutcome.ALREADY_IN_RECALL,
    )
    diagnostics = _expansion_diagnostics(status="no_eligible_modifiers", records=[record])
    assert diagnostics["modifies_authority_scope_status"] == "unscoped_relationships"
    assert diagnostics["modifies_authority_unscoped_count"] == 1

    scoped = _expansion_diagnostics(
        status="no_eligible_modifiers",
        records=[replace(record, target_provisions=("Section 21",))],
    )
    assert scoped["modifies_authority_scope_status"] == "scoped"


class _CountingReranker:
    provider_name = "test"
    model_name = "test-reranker"
    provider_version = "1"
    is_passthrough = False

    def __init__(self) -> None:
        self.requests = []

    async def rerank(self, request):
        self.requests.append(request)
        return RerankResponse(
            results=[
                RerankResult(chunk_id=item.chunk_id, score=0.9 - index * 0.01)
                for index, item in enumerate(request.candidates)
            ],
            provider=self.provider_name,
            model=self.model_name,
            provider_version=self.provider_version,
            score_scale=RerankScoreScale.MODEL_RELEVANCE,
        )


async def test_base_and_bounded_related_candidates_share_one_reranker_call() -> None:
    project_id = uuid.uuid4()
    build_id = uuid.uuid4()
    base_revision_id = uuid.uuid4()
    base_document_id = uuid.uuid4()
    base_chunk_id = uuid.uuid4()
    modifier_documents = [uuid.uuid4(), uuid.uuid4()]
    modifier_revisions = [uuid.uuid4(), uuid.uuid4()]
    modifier_chunks = [uuid.uuid4(), uuid.uuid4()]
    records = [
        _record(
            base_revision_id=base_revision_id,
            base_document_id=base_document_id,
            modifier_revision_id=revision_id,
            modifier_document_id=document_id,
        )
        for revision_id, document_id in zip(modifier_revisions, modifier_documents, strict=True)
    ]
    base_hit = CandidateHit(
        chunk_id=base_chunk_id,
        score=0.8,
        source=CandidateSource.SEMANTIC,
        semantic_score=0.8,
        metadata={
            "source_revision_id": str(base_revision_id),
            "source_document_id": str(base_document_id),
        },
    )
    related_hits = [
        CandidateHit(
            chunk_id=chunk_id,
            score=0.7,
            source=CandidateSource.SEMANTIC,
            semantic_score=0.7,
            metadata={
                "source_revision_id": str(revision_id),
                "source_document_id": str(document_id),
            },
        )
        for chunk_id, revision_id, document_id in zip(
            modifier_chunks,
            modifier_revisions,
            modifier_documents,
            strict=True,
        )
    ]
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever._semantic = AsyncMock()
    retriever._semantic.retrieve_batch.side_effect = [
        SemanticRetrievalBatch(
            hits=[base_hit],
            query_vector=[0.1, 0.2],
            provider="test",
            model="embedding",
        ),
        SemanticRetrievalBatch(
            hits=related_hits,
            query_vector=[0.1, 0.2],
            provider="test",
            model="embedding",
        ),
    ]
    retriever._semantic.score_chunk_ids.return_value = {}
    retriever._keyword = AsyncMock()
    retriever._keyword.retrieve.side_effect = [[], []]
    retriever._content_loader = AsyncMock()
    retriever._content_loader.load_texts.return_value = {
        base_chunk_id: "current policy",
        modifier_chunks[0]: "current amendment one",
        modifier_chunks[1]: "current amendment two",
    }
    retriever._embedder = AsyncMock()
    retriever._reranker = _CountingReranker()
    source_reader = AsyncMock()
    source_reader.incoming_modifiers.return_value = records
    context = RetrievalContext(
        project_id=project_id,
        query="current policy",
        embedding_set_version=2,
        filters=RetrievalFilters(),
        top_k=5,
        strategy=RetrievalStrategy.HYBRID,
        semantic_candidate_top_k=10,
        keyword_candidate_top_k=10,
        rrf_k=60,
        semantic_weight=1.0,
        keyword_weight=1.0,
        rerank_enabled=True,
        rerank_top_n=5,
        rerank_score_threshold=None,
        score_threshold=None,
        filterable_metadata_keys=(),
        index_build_id=build_id,
        rerank_candidate_window=5,
        rerank_return_n=5,
        source_scope=SimpleNamespace(generation=7, explicit_as_of=None),
        source_metadata_reader=source_reader,
        modifies_expansion_enabled=True,
        modifies_expansion_mode=ModifiesExpansionMode.EXPAND,
        max_related_sources=8,
        max_relationship_candidates=1,
    )

    results = await retriever.retrieve(context)

    assert len(retriever._reranker.requests) == 1
    reranked_ids = {item.chunk_id for item in retriever._reranker.requests[0].candidates}
    assert base_chunk_id in reranked_ids
    assert len(reranked_ids & set(modifier_chunks)) == 1
    related_call_context = retriever._semantic.retrieve_batch.await_args_list[1].args[0]
    assert set(related_call_context.filters.document_ids) == set(modifier_documents)
    related_result = next(item for item in results if item.chunk_id in modifier_chunks)
    assert related_result.metadata["relationship_grounding_trust"] is False
    assert related_result.metadata["relationship_recall_provenance"][0]["depth"] == 1
    assert {record["outcome"] for record in results[0].metadata["modifies_expansion_records"]} == {
        "expanded",
        "candidate_cap_exceeded",
    }
    assert results[0].metadata["relationship_candidate_count"] == 1
    assert any(
        contribution.branch_id.startswith("related_modifier:")
        for contribution in related_result.branch_contributions
    )


def _expansion_harness(
    *,
    document_id: uuid.UUID | None,
    mode: ModifiesExpansionMode,
) -> tuple[HybridRetriever, RetrievalContext, uuid.UUID, list[uuid.UUID]]:
    project_id = uuid.uuid4()
    build_id = uuid.uuid4()
    base_revision_id = uuid.uuid4()
    base_document_id = document_id or uuid.uuid4()
    base_chunk_id = uuid.uuid4()
    modifier_document_id = uuid.uuid4()
    modifier_chunk_id = uuid.uuid4()
    records = [
        _record(
            base_revision_id=base_revision_id,
            base_document_id=base_document_id,
            modifier_document_id=modifier_document_id,
        )
    ]
    base_hit = CandidateHit(
        chunk_id=base_chunk_id,
        score=0.8,
        source=CandidateSource.SEMANTIC,
        semantic_score=0.8,
        metadata={
            "source_revision_id": str(base_revision_id),
            "source_document_id": str(base_document_id),
        },
    )
    related_hit = CandidateHit(
        chunk_id=modifier_chunk_id,
        score=0.7,
        source=CandidateSource.SEMANTIC,
        semantic_score=0.7,
        metadata={
            "source_revision_id": str(records[0].modifier_revision_id),
            "source_document_id": str(modifier_document_id),
        },
    )
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever._semantic = AsyncMock()
    retriever._semantic.retrieve_batch.side_effect = [
        SemanticRetrievalBatch(
            hits=[base_hit],
            query_vector=[0.1, 0.2],
            provider="test",
            model="embedding",
        ),
        SemanticRetrievalBatch(
            hits=[related_hit],
            query_vector=[0.1, 0.2],
            provider="test",
            model="embedding",
        ),
    ]
    retriever._semantic.score_chunk_ids.return_value = {}
    retriever._keyword = AsyncMock()
    retriever._keyword.retrieve.side_effect = [[], []]
    retriever._content_loader = AsyncMock()
    retriever._content_loader.load_texts.return_value = {
        base_chunk_id: "current policy",
        modifier_chunk_id: "current amendment",
    }
    retriever._embedder = AsyncMock()
    retriever._reranker = _CountingReranker()
    source_reader = AsyncMock()
    source_reader.incoming_modifiers.return_value = records
    context = RetrievalContext(
        project_id=project_id,
        query="current policy",
        embedding_set_version=2,
        filters=RetrievalFilters(document_id=document_id),
        top_k=5,
        strategy=RetrievalStrategy.HYBRID,
        semantic_candidate_top_k=10,
        keyword_candidate_top_k=10,
        rrf_k=60,
        semantic_weight=1.0,
        keyword_weight=1.0,
        rerank_enabled=True,
        rerank_top_n=5,
        rerank_score_threshold=None,
        score_threshold=None,
        filterable_metadata_keys=(),
        index_build_id=build_id,
        rerank_candidate_window=5,
        rerank_return_n=5,
        source_scope=SimpleNamespace(generation=7, explicit_as_of=None),
        source_metadata_reader=source_reader,
        modifies_expansion_enabled=mode is ModifiesExpansionMode.EXPAND,
        modifies_expansion_mode=mode,
        max_related_sources=8,
        max_relationship_candidates=20,
    )
    return retriever, context, modifier_chunk_id, modifier_document_id


async def test_observe_mode_reports_eligible_modifiers_without_changing_recall() -> None:
    retriever, context, modifier_chunk_id, _modifier_document_id = _expansion_harness(
        document_id=None,
        mode=ModifiesExpansionMode.OBSERVE,
    )

    results = await retriever.retrieve(context)

    assert modifier_chunk_id not in {item.chunk_id for item in results}
    assert retriever._semantic.retrieve_batch.await_count == 1
    assert results[0].metadata["modifies_expansion_status"] == "observe"
    assert results[0].metadata["modifies_expansion_records"][0]["outcome"] == "expanded"
    assert results[0].metadata["related_source_count"] == 1
    assert results[0].metadata["relationship_candidate_count"] == 0


async def test_document_id_scope_does_not_retrieve_modifier_documents() -> None:
    scoped_document_id = uuid.uuid4()
    retriever, context, modifier_chunk_id, _modifier_document_id = _expansion_harness(
        document_id=scoped_document_id,
        mode=ModifiesExpansionMode.EXPAND,
    )

    results = await retriever.retrieve(context)

    assert modifier_chunk_id not in {item.chunk_id for item in results}
    assert retriever._semantic.retrieve_batch.await_count == 1
    assert results[0].metadata["modifies_expansion_status"] == "suppressed_document_scope"
    assert results[0].metadata["modifies_expansion_records"][0]["outcome"] == "expanded"
