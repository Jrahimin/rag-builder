"""Unit tests for ContextBuilder."""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from app.core.config import ChatConfig
from app.modules.conversations.citation_snapshots import (
    EVIDENCE_PROVENANCE_VERSION,
    build_citation_snapshots,
)
from app.modules.conversations.context_builder import ContextBuilder, compliance_overview_requested
from app.modules.conversations.grounded_context import reconstruct_reused_evidence
from app.modules.conversations.ports import ContextChunk
from app.platform.domain.content_hash import content_hash

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "question, expected",
    [
        (
            "What legal, regulatory, tax, and annual compliance obligations does it still have?",
            True,
        ),
        ("Which filings are required for an inactive company?", True),
        ("কোম্পানির কী কী বাধ্যবাধকতা আছে?", True),
        ("বার্ষিক পরিপালন চেকলিস্ট দিন", True),
        ("When is the first AGM due?", False),
        ("Calculate income tax on taxable income of 900000.", False),
    ],
)
def test_compliance_overview_requires_review(question, expected):
    assert compliance_overview_requested(question) is expected


def _chunk(
    *,
    chunk_id: uuid.UUID | None = None,
    content: str = "hello",
    score: float = 1.0,
) -> ContextChunk:
    cid = chunk_id or uuid.uuid4()
    return ContextChunk(
        chunk_id=cid,
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=content,
        score=score,
        filename="doc.txt",
        chunk_hash=content_hash(content),
    )


def test_deduplicates_by_chunk_id() -> None:
    chunk_id = uuid.uuid4()
    builder = ContextBuilder(ChatConfig(max_context_chunks=5, context_char_budget=10_000))
    selected = builder.select([_chunk(chunk_id=chunk_id), _chunk(chunk_id=chunk_id, content="dup")])
    assert len(selected) == 1


def test_preserves_input_order() -> None:
    first = _chunk(content="first", score=0.9)
    second = _chunk(content="second", score=0.5)
    builder = ContextBuilder(ChatConfig(max_context_chunks=5, context_char_budget=10_000))
    selected = builder.select([first, second])
    assert [chunk.content for chunk in selected] == ["first", "second"]


def test_enforces_max_context_chunks() -> None:
    chunks = [_chunk(content=f"c{i}") for i in range(5)]
    builder = ContextBuilder(ChatConfig(max_context_chunks=2, context_char_budget=10_000))
    selected = builder.select(chunks)
    assert len(selected) == 2


def test_enforces_char_budget() -> None:
    chunks = [_chunk(content="a" * 600)]
    builder = ContextBuilder(ChatConfig(max_context_chunks=5, context_char_budget=500))
    selected = builder.select(chunks)
    assert len(selected) == 1
    assert len(selected[0].content) == 500


def test_trimmed_chunk_keeps_rerank_scores_for_evidence_gate() -> None:
    chunk_id = uuid.uuid4()
    ranked = ContextChunk(
        chunk_id=chunk_id,
        document_id=uuid.uuid4(),
        chunk_index=0,
        content="a" * 800,
        score=0.91,
        filename="doc.txt",
        chunk_hash=content_hash("a" * 800),
        semantic_score=0.22,
        rank_score=0.91,
        rerank_relevance_score=0.91,
        evidence_relevance_score=0.91,
        metadata={"rerank_status": "applied"},
    )
    builder = ContextBuilder(ChatConfig(max_context_chunks=5, context_char_budget=500))
    selected = builder.select([ranked])
    assert len(selected[0].content) == 500
    assert selected[0].rerank_relevance_score == 0.91
    assert selected[0].rank_score == 0.91
    assert selected[0].metadata["rerank_status"] == "applied"


def test_selected_context_restores_dropped_rerank_relevance_score() -> None:
    ranked = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content="gazette table",
        score=0.8693157,
        filename="doc.txt",
        chunk_hash=content_hash("gazette table"),
        semantic_score=0.32,
        rank_score=None,
        rerank_relevance_score=None,
        metadata={"rerank_status": "applied"},
    )
    selected = ContextBuilder(ChatConfig()).select([ranked])
    assert selected[0].rerank_relevance_score == pytest.approx(0.8693157)
    assert selected[0].rank_score == pytest.approx(0.8693157)


def test_keeps_the_highest_ranked_chunks_inside_the_context_budget() -> None:
    relevant = _chunk(content="relevant table body", score=0.9)
    extras = [_chunk(content=f"other-{index}", score=0.1) for index in range(9)]
    builder = ContextBuilder(ChatConfig(max_context_chunks=8, context_char_budget=10_000))

    selected = builder.select([relevant, *extras])

    assert relevant in selected
    assert len(selected) == 8
    assert extras[-1] not in selected


def test_deduplicates_by_chunk_hash() -> None:
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    shared_hash = content_hash("duplicate-content")
    builder = ContextBuilder(ChatConfig(max_context_chunks=5, context_char_budget=10_000))
    selected = builder.select(
        [
            ContextChunk(
                chunk_id=first_id,
                document_id=uuid.uuid4(),
                chunk_index=0,
                content="first",
                score=0.9,
                filename="doc.txt",
                chunk_hash=shared_hash,
            ),
            ContextChunk(
                chunk_id=second_id,
                document_id=uuid.uuid4(),
                chunk_index=0,
                content="second",
                score=0.8,
                filename="doc.txt",
                chunk_hash=shared_hash,
            ),
        ]
    )
    assert len(selected) == 1


def test_reconstruct_reused_evidence_rejects_missing_provenance_and_overflow() -> None:
    content = "A" * 800
    raw_hash = content_hash(content)
    chunk = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=content,
        score=0.9,
        filename="doc.txt",
        chunk_hash=raw_hash,
        metadata={"indexed_chunk_hash": raw_hash, "configuration_hash": "c" * 64},
    )
    missing = reconstruct_reused_evidence(
        chunks=[chunk],
        citations=[{"chunk_id": str(chunk.chunk_id), "source_kind": "knowledge"}],
        expansion_records=[],
        context_builder=ContextBuilder(ChatConfig()),
    )
    assert missing[0] == []
    assert missing[1]["reuse_failure_category"] == "missing_provenance"

    citation = {
        "chunk_id": str(chunk.chunk_id),
        "source_kind": "knowledge",
        "evidence_provenance_version": EVIDENCE_PROVENANCE_VERSION,
        "indexed_chunk_hash": raw_hash,
        "evidence_source_chunk_hash": raw_hash,
        "evidence_span_hash": raw_hash,
        "evidence_chunk_char_start": 0,
        "evidence_chunk_char_end": len(content),
        "evidence_span_derivation": "complete_chunk",
        "configuration_hash": "c" * 64,
        "document_id": str(chunk.document_id),
    }
    overflow = reconstruct_reused_evidence(
        chunks=[chunk],
        citations=[citation],
        expansion_records=[],
        context_builder=ContextBuilder(ChatConfig(context_char_budget=500)),
    )
    assert overflow[0] == []
    assert overflow[1]["reuse_failure_category"] == "context_overflow"

    reused, diagnostics = reconstruct_reused_evidence(
        chunks=[chunk],
        citations=[citation],
        expansion_records=[],
        context_builder=ContextBuilder(ChatConfig(context_char_budget=12_000)),
    )
    assert len(reused) == 1
    assert diagnostics["reuse_validation_outcome"] == "passed"


def test_reconstruct_rejects_changed_configuration_but_ignores_project_revision() -> None:
    content = "refund within 30 days"
    raw_hash = content_hash(content)
    chunk = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=content,
        score=0.9,
        filename="policy.txt",
        chunk_hash=raw_hash,
        metadata={"indexed_chunk_hash": raw_hash, "configuration_hash": "d" * 64},
    )
    citation = {
        "chunk_id": str(chunk.chunk_id),
        "source_kind": "knowledge",
        "evidence_provenance_version": EVIDENCE_PROVENANCE_VERSION,
        "indexed_chunk_hash": raw_hash,
        "evidence_source_chunk_hash": raw_hash,
        "evidence_span_hash": raw_hash,
        "evidence_chunk_char_end": len(content),
        "evidence_chunk_char_start": 0,
        "configuration_hash": "e" * 64,
        "document_id": str(chunk.document_id),
        "config_provenance": {"project_config_revision_number": 1},
    }
    failed, diagnostics = reconstruct_reused_evidence(
        chunks=[chunk],
        citations=[citation],
        expansion_records=[],
        context_builder=ContextBuilder(ChatConfig()),
        current_configuration_hash="d" * 64,
    )
    assert failed == []
    assert diagnostics["reuse_failure_category"] == "configuration_changed"

    citation["configuration_hash"] = "d" * 64
    reused, passed = reconstruct_reused_evidence(
        chunks=[chunk],
        citations=[citation],
        expansion_records=[],
        context_builder=ContextBuilder(ChatConfig()),
        current_configuration_hash="d" * 64,
        current_config_snapshot_id=uuid.uuid4(),
    )
    assert len(reused) == 1
    assert passed["reuse_validation_outcome"] == "passed"


def test_reconstruct_declines_unreproducible_table_envelope() -> None:
    content = "Header | Value\n10 | 20"
    raw_hash = content_hash(content)
    chunk = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=content,
        score=0.9,
        filename="table.txt",
        chunk_hash=raw_hash,
        metadata={"indexed_chunk_hash": raw_hash},
    )
    citation = {
        "chunk_id": str(chunk.chunk_id),
        "source_kind": "knowledge",
        "evidence_provenance_version": EVIDENCE_PROVENANCE_VERSION,
        "indexed_chunk_hash": raw_hash,
        "evidence_source_chunk_hash": raw_hash,
        "evidence_span_hash": raw_hash,
        "evidence_chunk_char_start": 0,
        "evidence_chunk_char_end": len(content),
        "evidence_source_envelope": "reconstructed_context",
        "document_id": str(chunk.document_id),
    }
    units, diagnostics = reconstruct_reused_evidence(
        chunks=[chunk],
        citations=[citation],
        expansion_records=[],
        context_builder=ContextBuilder(ChatConfig()),
    )
    assert units == []
    assert diagnostics["reuse_failure_category"] == "unreproducible_derivation"


def test_reconstruct_accepts_reproducible_table_envelope() -> None:
    content = "Header | Value\n10 | 20"
    raw_hash = content_hash(content)
    chunk = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=content,
        score=0.9,
        filename="table.txt",
        chunk_hash=raw_hash,
        metadata={"indexed_chunk_hash": raw_hash, "table_context": "Schedule 1 heading"},
    )
    citation = {
        "chunk_id": str(chunk.chunk_id),
        "source_kind": "knowledge",
        "evidence_provenance_version": EVIDENCE_PROVENANCE_VERSION,
        "indexed_chunk_hash": raw_hash,
        "evidence_source_chunk_hash": raw_hash,
        "evidence_span_hash": raw_hash,
        "evidence_chunk_char_start": 0,
        "evidence_chunk_char_end": len(content),
        "evidence_source_envelope": "reconstructed_context",
        "document_id": str(chunk.document_id),
    }
    units, diagnostics = reconstruct_reused_evidence(
        chunks=[chunk],
        citations=[citation],
        expansion_records=[],
        context_builder=ContextBuilder(ChatConfig()),
    )
    assert len(units) == 1
    assert diagnostics["reuse_validation_outcome"] == "passed"


def test_reconstruct_exits_when_a_new_modifier_is_unrecorded() -> None:
    content = "Section 1 remains in force."
    raw_hash = content_hash(content)
    modifier = str(uuid.uuid4())
    chunk = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=content,
        score=0.9,
        filename="act.txt",
        chunk_hash=raw_hash,
        metadata={
            "indexed_chunk_hash": raw_hash,
            "source_revision_id": str(uuid.uuid4()),
            "source_relationships": [
                {
                    "relationship_type": "modifies",
                    "direction": "incoming",
                    "source_revision_id": modifier,
                }
            ],
        },
    )
    citation = {
        "chunk_id": str(chunk.chunk_id),
        "source_kind": "knowledge",
        "evidence_provenance_version": EVIDENCE_PROVENANCE_VERSION,
        "indexed_chunk_hash": raw_hash,
        "evidence_source_chunk_hash": raw_hash,
        "evidence_span_hash": raw_hash,
        "evidence_chunk_char_start": 0,
        "evidence_chunk_char_end": len(content),
        "relationship_recall_provenance": [],
        "document_id": str(chunk.document_id),
    }
    units, diagnostics = reconstruct_reused_evidence(
        chunks=[chunk],
        citations=[citation],
        expansion_records=[],
        context_builder=ContextBuilder(ChatConfig()),
    )
    assert units == []
    assert diagnostics["reuse_failure_category"] == "unrecorded_authority_dependency"


def test_reconstruct_rejects_document_and_content_changes() -> None:
    content = "Filing is due within 30 days."
    raw_hash = content_hash(content)
    chunk = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=content,
        score=0.9,
        filename="act.txt",
        chunk_hash=raw_hash,
        metadata={"indexed_chunk_hash": raw_hash},
    )
    citation = {
        "chunk_id": str(chunk.chunk_id),
        "source_kind": "knowledge",
        "evidence_provenance_version": EVIDENCE_PROVENANCE_VERSION,
        "indexed_chunk_hash": raw_hash,
        "evidence_source_chunk_hash": raw_hash,
        "evidence_span_hash": raw_hash,
        "evidence_chunk_char_start": 0,
        "evidence_chunk_char_end": len(content),
        "document_id": str(uuid.uuid4()),
    }
    mismatched, document_diag = reconstruct_reused_evidence(
        chunks=[chunk],
        citations=[citation],
        expansion_records=[],
        context_builder=ContextBuilder(ChatConfig()),
    )
    assert mismatched == []
    assert document_diag["reuse_failure_category"] == "document_mismatch"

    citation["document_id"] = str(chunk.document_id)
    changed = replace(chunk, content=content + " amended", chunk_hash=content_hash(content + " x"))
    stale, hash_diag = reconstruct_reused_evidence(
        chunks=[changed],
        citations=[citation],
        expansion_records=[],
        context_builder=ContextBuilder(ChatConfig()),
    )
    assert stale == []
    assert hash_diag["reuse_failure_category"] == "hash_mismatch"

    missing, identity_diag = reconstruct_reused_evidence(
        chunks=[],
        citations=[citation],
        expansion_records=[],
        context_builder=ContextBuilder(ChatConfig()),
    )
    assert missing == []
    assert identity_diag["reuse_failure_category"] == "missing_identity"


def test_reconstruct_survives_index_build_change_when_hashes_match() -> None:
    content = "Refunds remain available for 30 days."
    raw_hash = content_hash(content)
    chunk = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=content,
        score=0.9,
        filename="policy.txt",
        chunk_hash=raw_hash,
        metadata={
            "indexed_chunk_hash": raw_hash,
            "index_build_id": str(uuid.uuid4()),
            "source_metadata_generation": 4,
        },
    )
    citation = {
        "chunk_id": str(chunk.chunk_id),
        "source_kind": "knowledge",
        "evidence_provenance_version": EVIDENCE_PROVENANCE_VERSION,
        "indexed_chunk_hash": raw_hash,
        "evidence_source_chunk_hash": raw_hash,
        "evidence_span_hash": raw_hash,
        "evidence_chunk_char_start": 0,
        "evidence_chunk_char_end": len(content),
        "document_id": str(chunk.document_id),
        "index_build_id": str(uuid.uuid4()),
        "source_metadata_generation": 3,
    }
    reused, diagnostics = reconstruct_reused_evidence(
        chunks=[chunk],
        citations=[citation],
        expansion_records=[],
        context_builder=ContextBuilder(ChatConfig()),
    )
    assert len(reused) == 1
    assert diagnostics["reuse_validation_outcome"] == "passed"


def test_reconstruct_survives_index_generation_change_when_hashes_match() -> None:
    content = "refund within 30 days"
    raw_hash = content_hash(content)
    chunk = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=content,
        score=0.9,
        filename="policy.txt",
        chunk_hash=raw_hash,
        metadata={
            "indexed_chunk_hash": raw_hash,
            "index_build_id": str(uuid.uuid4()),
            "source_metadata_generation": 4,
            "processing_version": 2,
            "configuration_hash": "f" * 64,
        },
    )
    citation = {
        "chunk_id": str(chunk.chunk_id),
        "source_kind": "knowledge",
        "evidence_provenance_version": EVIDENCE_PROVENANCE_VERSION,
        "indexed_chunk_hash": raw_hash,
        "evidence_source_chunk_hash": raw_hash,
        "evidence_span_hash": raw_hash,
        "evidence_chunk_char_start": 0,
        "evidence_chunk_char_end": len(content),
        "index_build_id": str(uuid.uuid4()),
        "source_metadata_generation": 1,
        "processing_version": 1,
        "configuration_hash": "f" * 64,
        "document_id": str(chunk.document_id),
    }
    reused, diagnostics = reconstruct_reused_evidence(
        chunks=[chunk],
        citations=[citation],
        expansion_records=[],
        context_builder=ContextBuilder(ChatConfig()),
        current_configuration_hash="f" * 64,
    )
    assert len(reused) == 1
    assert diagnostics["reuse_validation_outcome"] == "passed"


def test_reconstruct_rejects_identities_missing_from_current_index() -> None:
    content = "refund within 30 days"
    raw_hash = content_hash(content)
    chunk_id = uuid.uuid4()
    citation = {
        "chunk_id": str(chunk_id),
        "source_kind": "knowledge",
        "evidence_provenance_version": EVIDENCE_PROVENANCE_VERSION,
        "indexed_chunk_hash": raw_hash,
        "evidence_source_chunk_hash": raw_hash,
        "evidence_span_hash": raw_hash,
        "evidence_chunk_char_start": 0,
        "evidence_chunk_char_end": len(content),
        "document_id": str(uuid.uuid4()),
    }
    units, diagnostics = reconstruct_reused_evidence(
        chunks=[],
        citations=[citation],
        expansion_records=[],
        context_builder=ContextBuilder(ChatConfig()),
    )
    assert units == []
    assert diagnostics["reuse_failure_category"] == "missing_identity"


def test_reconstruct_rejects_changed_relationship_meaning_despite_matching_text() -> None:
    content = "Section 21 — Rebate\nThe rebate is 15%."
    raw_hash = content_hash(content)
    base_revision = uuid.uuid4()
    modifier = uuid.uuid4()
    relationship = uuid.uuid4()
    chunk = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=content,
        score=0.9,
        filename="act.txt",
        chunk_hash=raw_hash,
        metadata={
            "indexed_chunk_hash": raw_hash,
            "source_revision_id": str(base_revision),
        },
    )
    saved = {
        "relationship_id": str(relationship),
        "base_revision_id": str(base_revision),
        "modifier_revision_id": str(modifier),
        "target_provisions": ["Section 21 — Rebate"],
        "modifier_effective_from": "2020-01-01",
        "modifier_effective_to": None,
        "base_effective_from": "2018-01-01",
        "base_effective_to": None,
    }
    citation = {
        "chunk_id": str(chunk.chunk_id),
        "source_kind": "knowledge",
        "evidence_provenance_version": EVIDENCE_PROVENANCE_VERSION,
        "indexed_chunk_hash": raw_hash,
        "evidence_source_chunk_hash": raw_hash,
        "evidence_span_hash": raw_hash,
        "evidence_chunk_char_start": 0,
        "evidence_chunk_char_end": len(content),
        "document_id": str(chunk.document_id),
        "relationship_recall_provenance": [saved],
    }
    changed_scope = {
        **saved,
        "outcome": "already_in_recall",
        "target_provisions": ["Section 22 — Limit"],
    }
    failed, diagnostics = reconstruct_reused_evidence(
        chunks=[chunk],
        citations=[citation],
        expansion_records=[changed_scope],
        context_builder=ContextBuilder(ChatConfig()),
    )
    assert failed == []
    assert diagnostics["reuse_failure_category"] == "authority_dependency_changed"

    disjoint = {
        **saved,
        "outcome": "already_in_recall",
        "target_provisions": ["Section 99 — Other"],
    }
    citation["relationship_recall_provenance"] = [
        {**saved, "target_provisions": ["Section 99 — Other"]}
    ]
    reused, passed = reconstruct_reused_evidence(
        chunks=[chunk],
        citations=[citation],
        expansion_records=[disjoint],
        context_builder=ContextBuilder(ChatConfig()),
    )
    assert len(reused) == 1
    assert passed["reuse_validation_outcome"] == "passed"

    citation["relationship_recall_provenance"] = [saved]
    missing_current, missing_diag = reconstruct_reused_evidence(
        chunks=[chunk],
        citations=[citation],
        expansion_records=[],
        context_builder=ContextBuilder(ChatConfig()),
    )
    assert missing_current == []
    assert missing_diag["reuse_failure_category"] == "authority_dependency_changed"


def test_reconstruct_reuses_governed_base_when_recorded_modifier_is_recalled() -> None:
    from app.modules.conversations.current_authority import remove_superseded_provisions

    base_revision = uuid.uuid4()
    modifier_revision = uuid.uuid4()
    base_content = (
        "Section 20 — Eligible Investment\nApproved savings certificates.\n\n"
        "Section 21 — Investment Rebate Rate\nThe rebate is 15%.\n\n"
        "Section 22 — Rebate Limit\nThe rebate cannot exceed tax liability."
    )
    modifier_content = "Section 21 — Investment Rebate Rate\nThe rebate is 10%."
    raw_hash = content_hash(base_content)
    base = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=base_content,
        score=0.9,
        filename="act.txt",
        chunk_hash=raw_hash,
        metadata={"indexed_chunk_hash": raw_hash, "source_revision_id": str(base_revision)},
    )
    modifier = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=modifier_content,
        score=0.8,
        filename="amendment.txt",
        chunk_hash=content_hash(modifier_content),
        metadata={
            "indexed_chunk_hash": content_hash(modifier_content),
            "source_revision_id": str(modifier_revision),
            "retrieval_scope": "related_modifier",
        },
    )
    record = {
        "relationship_id": str(uuid.uuid4()),
        "relationship_type": "modifies",
        "outcome": "expanded",
        "base_revision_id": str(base_revision),
        "modifier_revision_id": str(modifier_revision),
        "target_provisions": ["Section 21 — Investment Rebate Rate"],
        "modifier_effective_from": "2020-01-01",
        "modifier_recalled": True,
    }
    redacted = next(
        chunk
        for chunk in remove_superseded_provisions([base, modifier], [record])
        if chunk.chunk_id == base.chunk_id
    )
    source_hash = content_hash(redacted.content)
    citation = {
        "chunk_id": str(base.chunk_id),
        "source_kind": "knowledge",
        "evidence_provenance_version": EVIDENCE_PROVENANCE_VERSION,
        "indexed_chunk_hash": raw_hash,
        "evidence_source_chunk_hash": source_hash,
        "evidence_span_hash": source_hash,
        "evidence_chunk_char_start": 0,
        "evidence_chunk_char_end": len(redacted.content),
        "document_id": str(base.document_id),
        "authority_dependencies": [record],
    }
    missing, missing_diag = reconstruct_reused_evidence(
        chunks=[base],
        citations=[citation],
        expansion_records=[record],
        context_builder=ContextBuilder(ChatConfig()),
    )
    assert missing == []
    assert missing_diag["reuse_failure_category"] == "missing_identity"

    recalled_modifier = replace(
        modifier,
        metadata={
            key: value for key, value in modifier.metadata.items() if key != "retrieval_scope"
        },
    )
    reused, diagnostics = reconstruct_reused_evidence(
        chunks=[base, recalled_modifier],
        citations=[citation],
        expansion_records=[record],
        context_builder=ContextBuilder(ChatConfig()),
    )
    assert len(reused) == 1
    assert reused[0].chunk_id == base.chunk_id
    assert diagnostics["reuse_validation_outcome"] == "passed"


def test_reconstruct_reuses_cited_modifier_after_identity_recall() -> None:
    from app.modules.conversations.current_authority import remove_superseded_provisions

    base_revision = uuid.uuid4()
    modifier_revision = uuid.uuid4()
    base_content = (
        "Section 21 — Investment Rebate Rate\nThe rebate is 15%.\n\n"
        "Section 22 — Rebate Limit\nThe rebate cannot exceed tax liability."
    )
    modifier_content = (
        "Section 5 — Amendment of section 21\nIn section 21, for '15%' substitute '10%'."
    )
    base = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=base_content,
        score=0.9,
        filename="act.txt",
        chunk_hash=content_hash(base_content),
        metadata={
            "indexed_chunk_hash": content_hash(base_content),
            "source_revision_id": str(base_revision),
            "configuration_hash": "a" * 64,
        },
    )
    modifier = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=modifier_content,
        score=0.8,
        filename="amendment.txt",
        chunk_hash=content_hash(modifier_content),
        metadata={
            "indexed_chunk_hash": content_hash(modifier_content),
            "source_revision_id": str(modifier_revision),
            "retrieval_scope": "related_modifier",
            "configuration_hash": "a" * 64,
        },
    )
    record = {
        "relationship_id": str(uuid.uuid4()),
        "relationship_type": "modifies",
        "outcome": "expanded",
        "base_revision_id": str(base_revision),
        "modifier_revision_id": str(modifier_revision),
        "target_provisions": ["Section 21 — Investment Rebate Rate"],
        "modifier_effective_from": "2020-01-01",
    }
    stale = {
        **record,
        "relationship_id": str(uuid.uuid4()),
        "modifier_revision_id": str(uuid.uuid4()),
        "outcome": "stale_or_replaced_revision",
    }
    safe = remove_superseded_provisions([base, modifier], [record, stale])
    prepared = []
    for chunk in safe:
        prepared.append(
            replace(
                chunk,
                metadata={
                    **chunk.metadata,
                    "evidence_unit_id": str(uuid.uuid4()),
                    "evidence_span_hash": content_hash(chunk.content),
                    "evidence_chunk_char_start": 0,
                    "evidence_chunk_char_end": len(chunk.content),
                    "evidence_source_chunk_hash": content_hash(chunk.content),
                },
            )
        )
    citations = build_citation_snapshots(
        prepared,
        config=ChatConfig(),
        project_id=uuid.uuid4(),
        config_snapshot_id=None,
        config_provenance={},
        prompt_version="v18",
        expansion_records=[record, stale],
        recalled_chunks=[base, modifier],
    )
    recalled = [
        replace(
            chunk,
            metadata={
                key: value
                for key, value in chunk.metadata.items()
                if key
                not in {
                    "retrieval_scope",
                    "relationship_recall_provenance",
                    "relationship_grounding_trust",
                }
            },
        )
        for chunk in (base, modifier)
    ]
    reused, diagnostics = reconstruct_reused_evidence(
        chunks=recalled,
        citations=citations,
        expansion_records=[record, stale],
        context_builder=ContextBuilder(ChatConfig()),
    )
    assert diagnostics["reuse_validation_outcome"] == "passed"
    assert {unit.chunk_id for unit in reused} == {item.chunk_id for item in prepared}


def test_reconstruct_normalizes_authority_dates_before_comparing() -> None:
    content = "Section 21 — Rebate\nThe rebate is 15%."
    raw_hash = content_hash(content)
    base_revision = uuid.uuid4()
    chunk = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=content,
        score=0.9,
        filename="act.txt",
        chunk_hash=raw_hash,
        metadata={"indexed_chunk_hash": raw_hash, "source_revision_id": str(base_revision)},
    )
    saved = {
        "relationship_id": "edge-21",
        "base_revision_id": str(base_revision),
        "modifier_revision_id": str(uuid.uuid4()),
        "target_provisions": ["Section 99 — Other"],
        "modifier_effective_from": "2020-01-01T00:00:00+00:00",
        "modifier_recalled": False,
        "outcome": "already_in_recall",
    }
    current = {
        **saved,
        "modifier_effective_from": "2020-01-01",
    }
    citation = {
        "chunk_id": str(chunk.chunk_id),
        "source_kind": "knowledge",
        "evidence_provenance_version": EVIDENCE_PROVENANCE_VERSION,
        "indexed_chunk_hash": raw_hash,
        "evidence_source_chunk_hash": raw_hash,
        "evidence_span_hash": raw_hash,
        "evidence_chunk_char_start": 0,
        "evidence_chunk_char_end": len(content),
        "document_id": str(chunk.document_id),
        "authority_dependencies": [saved],
    }
    reused, diagnostics = reconstruct_reused_evidence(
        chunks=[chunk],
        citations=[citation],
        expansion_records=[current],
        context_builder=ContextBuilder(ChatConfig()),
    )
    assert len(reused) == 1
    assert diagnostics["reuse_validation_outcome"] == "passed"
