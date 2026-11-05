"""Governance regressions for append-only metadata corrections."""

import uuid
from datetime import date
from types import SimpleNamespace

from app.modules.knowledge.source_metadata_read import _modifier_outcome


def test_stale_incomplete_modifier_cannot_poison_corrected_current_revision():
    project = uuid.uuid4()
    old, current = uuid.uuid4(), uuid.uuid4()
    row = SimpleNamespace(
        relationship_project_id=project,
        modifier_project_id=project,
        base_project_id=project,
        modifier_revision_id=old,
        modifier_effective_from=None,
    )
    assert (
        _modifier_outcome(
            row,
            project_id=project,
            selected_revision=current,
            indexed=True,
            reference_date=date(2026, 9, 7),
        )
        == "stale_or_replaced_revision"
    )


def test_cross_project_edge_is_rejected_before_revision_resolution():
    project = uuid.uuid4()
    row = SimpleNamespace(
        relationship_project_id=uuid.uuid4(),
        modifier_project_id=project,
        base_project_id=project,
    )
    assert (
        _modifier_outcome(
            row,
            project_id=project,
            selected_revision=uuid.uuid4(),
            indexed=True,
            reference_date=date(2026, 9, 7),
        )
        == "cross_project_or_generation"
    )


async def test_sparse_title_correction_preserves_full_review_and_invalidate_changed_scope():
    from unittest.mock import AsyncMock, MagicMock

    from app.models.source_metadata import (
        SourceLifecycleStatus,
        SourceRelationshipType,
        SourceRevisionRelationship,
        SourceRole,
    )
    from app.modules.knowledge.schemas.source_metadata import SourceRevisionCreate
    from app.modules.knowledge.services.source_metadata_service import SourceMetadataService

    project, document_id, revision_id, group_id, target_id = [uuid.uuid4() for _ in range(5)]
    chunk_id = uuid.uuid4()
    quote = "Section 21 is amended subject to savings."
    provenance = {
        "reviewer": "operator-A",
        "reason": "Exact clause reviewed",
        "evidence_hash": "b" * 64,
    }
    edge = SourceRevisionRelationship(
        id=uuid.uuid4(),
        project_id=project,
        source_revision_id=revision_id,
        target_revision_id=target_id,
        relationship_type=SourceRelationshipType.MODIFIES,
        target_provisions=["Section 21"],
        provision_effect="amends",
        replacement_scope_verified=True,
        review_provenance=provenance,
        supporting_spans=[
            {"chunk_id": str(chunk_id), "quote": quote, "char_start": 0, "char_end": len(quote)}
        ],
    )
    old = SimpleNamespace(
        id=revision_id,
        document_id=document_id,
        source_group_id=group_id,
        content_hash="a" * 64,
        effective_from=date(2026, 7, 1),
        effective_to=None,
        published_date=date(2026, 8, 1),
        work_key="law",
        edition_key="amendment",
        title="Old",
        source_type="law",
        revision_label="1",
        lifecycle_status=SourceLifecycleStatus.ACTIVE,
        source_role=SourceRole.PRIMARY,
    )
    repo = MagicMock(project_id=project)
    repo.latest_for_document = AsyncMock(return_value=(None, old))
    repo.get_group = AsyncMock(return_value=SimpleNamespace(id=group_id))
    repo.next_revision_number = AsyncMock(return_value=2)
    repo.flush = AsyncMock()
    repo.relationships_for = AsyncMock(return_value={revision_id: [edge]})
    repo.get_revision = AsyncMock(
        return_value=SimpleNamespace(
            id=target_id,
            document_id=uuid.uuid4(),
            source_group_id=uuid.uuid4(),
            content_hash="c" * 64,
        )
    )
    repo.get_document = AsyncMock(return_value=object())
    session = AsyncMock()
    session.scalar.return_value = SimpleNamespace(content=quote)
    service = SourceMetadataService(session, repo)
    document = SimpleNamespace(
        id=document_id, filename="law.pdf", content_sha256="a" * 64, version=1
    )
    revision = await service._stage_revision(
        document, SourceRevisionCreate(title="Corrected title")
    )
    created = next(
        call.args[0]
        for call in repo.add.call_args_list
        if isinstance(call.args[0], SourceRevisionRelationship)
    )
    assert revision.effective_from == old.effective_from
    assert created.replacement_scope_verified is True
    assert created.supporting_spans == edge.supporting_spans
    assert all(created.review_provenance[k] == v for k, v in provenance.items())
    repo.add.reset_mock()
    await service._stage_revision(
        document,
        SourceRevisionCreate(
            title="Corrected",
            relationships=[
                {
                    "relationship_type": "modifies",
                    "target_revision_id": str(target_id),
                    "target_provisions": ["Section 22"],
                }
            ],
        ),
    )
    changed = next(
        call.args[0]
        for call in repo.add.call_args_list
        if isinstance(call.args[0], SourceRevisionRelationship)
    )
    assert changed.replacement_scope_verified is False
    assert changed.review_provenance["invalidated_reason"] == "proof_identity_changed"

    # Even a still-valid quote from the same chunk is a different reviewed proof.
    repo.add.reset_mock()
    changed_quote = "is amended"
    start = quote.index(changed_quote)
    await service._stage_revision(
        document,
        SourceRevisionCreate(
            relationships=[
                {
                    "relationship_type": "modifies",
                    "target_revision_id": str(target_id),
                    "target_provisions": ["Section 21"],
                    "provision_effect": "amends",
                    "replacement_scope_verified": True,
                    "review_provenance": provenance,
                    "supporting_spans": [
                        {
                            "chunk_id": str(chunk_id),
                            "quote": changed_quote,
                            "char_start": start,
                            "char_end": start + len(changed_quote),
                        }
                    ],
                }
            ]
        ),
    )
    changed_span = next(
        call.args[0]
        for call in repo.add.call_args_list
        if isinstance(call.args[0], SourceRevisionRelationship)
    )
    assert changed_span.replacement_scope_verified is False
    assert changed_span.review_provenance["invalidated_reason"] == "proof_identity_changed"
    assert "evidence_hash" not in changed_span.review_provenance
