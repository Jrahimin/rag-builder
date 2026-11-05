"""Request metadata cannot replace server identities or copied authenticated reviews."""

import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import BadRequestError
from app.models.source_metadata import (
    SourceLifecycleStatus,
    SourceRelationshipType,
    SourceRevisionRelationship,
    SourceRole,
)
from app.modules.knowledge.schemas.source_metadata import SourceRevisionCreate
from app.modules.knowledge.services.source_metadata_service import SourceMetadataService

pytestmark = pytest.mark.unit
PROTECTED = (
    "reviewer",
    "source_content_hash",
    "source_revision_id",
    "target_revision_id",
    "target_content_hash",
    "reason",
)


def fixture(*, prior=False, relationship_type="modifies"):
    project, document_id, revision_id, group_id, target_id, chunk_id = [
        uuid.uuid4() for _ in range(6)
    ]
    quote = "Section 21 is amended subject to savings."
    target = SimpleNamespace(
        id=target_id,
        document_id=uuid.uuid4(),
        source_group_id=group_id if relationship_type == "replaces" else uuid.uuid4(),
        content_hash="c" * 64,
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
    provenance = {
        "reviewer": "historical-operator",
        "source_content_hash": old.content_hash,
        "source_revision_id": str(old.id),
        "target_revision_id": str(target_id),
        "target_content_hash": target.content_hash,
        "reason": "Historical exact clause review",
        "evidence_hash": "b" * 64,
        "whole_document_replacement": "declared",
    }
    payload = {
        "relationship_type": relationship_type,
        "target_revision_id": str(target_id),
        "target_provisions": ["Section 21"] if relationship_type == "modifies" else [],
        "provision_effect": "amends" if relationship_type == "modifies" else "replaces",
        "replacement_scope_verified": True,
        "review_provenance": provenance,
        "supporting_spans": [
            {"chunk_id": str(chunk_id), "quote": quote, "char_start": 0, "char_end": len(quote)}
        ],
    }
    edge = SourceRevisionRelationship(
        id=uuid.uuid4(),
        project_id=project,
        source_revision_id=revision_id,
        **{
            **payload,
            "relationship_type": SourceRelationshipType(relationship_type),
            "target_revision_id": target_id,
        },
    )
    repo = MagicMock(project_id=project)
    repo.latest_for_document = AsyncMock(return_value=(None, old))
    repo.get_group = AsyncMock(return_value=SimpleNamespace(id=group_id))
    repo.next_revision_number = AsyncMock(return_value=2)
    repo.flush = AsyncMock()
    repo.relationships_for = AsyncMock(return_value={revision_id: [edge] if prior else []})
    repo.get_revision = AsyncMock(return_value=target)
    repo.get_document = AsyncMock(return_value=object())
    session = AsyncMock()
    session.scalar.return_value = SimpleNamespace(content=quote)
    service = SourceMetadataService(session, repo, actor_id="actual-operator")
    document = SimpleNamespace(
        id=document_id, filename="law.pdf", content_sha256=old.content_hash, version=1
    )
    return service, repo, document, old, target, edge, payload


async def stage(service, repo, document, data):
    revision = await service._stage_revision(document, SourceRevisionCreate.model_validate(data))
    created = next(
        call.args[0]
        for call in repo.add.call_args_list
        if isinstance(call.args[0], SourceRevisionRelationship)
    )
    return revision, created


@pytest.mark.parametrize("relationship_type", ["modifies", "replaces"])
@pytest.mark.parametrize("key", PROTECTED)
async def test_new_review_server_fields_cannot_be_spoofed(relationship_type, key):
    service, repo, document, _, target, _, payload = fixture(relationship_type=relationship_type)
    payload["review_provenance"] = {**payload["review_provenance"], key: "forged-value"}
    revision, created = await stage(
        service, repo, document, {"relationships": [payload], "change_reason": "Actual new review"}
    )
    expected = {
        "reviewer": "actual-operator",
        "source_content_hash": document.content_sha256,
        "source_revision_id": str(revision.id),
        "target_revision_id": str(target.id),
        "target_content_hash": target.content_hash,
        "reason": "Actual new review",
    }
    assert created.replacement_scope_verified
    assert all(created.review_provenance[k] == v for k, v in expected.items())
    assert created.review_provenance["evidence_hash"] == "b" * 64
    assert created.review_provenance["whole_document_replacement"] == "declared"


@pytest.mark.parametrize("sparse", [True, False])
async def test_unchanged_sparse_corrections_copy_persisted_review_exactly(sparse):
    service, repo, document, _, _, edge, payload = fixture(prior=True)
    data = {"title": "Corrected title", "change_reason": "Unrelated title edit"}
    if not sparse:
        data["relationships"] = [
            {
                "relationship_type": payload["relationship_type"],
                "target_revision_id": payload["target_revision_id"],
            }
        ]
    _, created = await stage(service, repo, document, data)
    assert created.replacement_scope_verified
    assert created.review_provenance == edge.review_provenance
    assert created.supporting_spans == edge.supporting_spans


async def test_request_fields_on_unchanged_edge_are_a_new_authenticated_review():
    service, repo, document, _, target, _, payload = fixture(prior=True)
    payload["review_provenance"] = dict.fromkeys(PROTECTED, "forged-value")
    revision, created = await stage(
        service, repo, document, {"relationships": [payload], "change_reason": "New review"}
    )
    assert created.review_provenance["reviewer"] == "actual-operator"
    assert created.review_provenance["source_revision_id"] == str(revision.id)
    assert created.review_provenance["target_revision_id"] == str(target.id)
    assert created.review_provenance["source_content_hash"] == document.content_sha256
    assert created.review_provenance["target_content_hash"] == target.content_hash
    assert created.review_provenance["reason"] == "New review"


async def test_new_review_default_reason_and_invalidation_status_are_server_owned():
    service, repo, document, _, _, _, payload = fixture()
    payload["review_provenance"] = {
        "reason": "Forged historical reason",
        "invalidated_reason": "Forged invalidation",
    }
    _, created = await stage(service, repo, document, {"relationships": [payload]})
    assert created.review_provenance["reason"] == "source relationship revision"
    assert "invalidated_reason" not in created.review_provenance


@pytest.mark.parametrize(
    "change",
    [
        "content",
        "period",
        "work",
        "edition",
        "group",
        "target",
        "operation",
        "relationship_type",
        "scope",
        "quote",
    ],
)
async def test_changed_proof_invalidates_old_review_instead_of_copying_it(change):
    service, repo, document, _, target, _, payload = fixture(prior=True)
    data = {"relationships": [payload], "change_reason": "Identity change"}
    if change == "content":
        document.content_sha256 = "d" * 64
    elif change == "period":
        data["effective_from"] = "2026-07-02"
    elif change in {"work", "edition"}:
        data[f"{change}_key"] = "different-identity"
    elif change == "group":
        data["create_new_group"] = True
    elif change == "target":
        target.id = uuid.uuid4()
        payload["target_revision_id"] = str(target.id)
    elif change == "operation":
        payload["provision_effect"] = "savings"
    elif change == "relationship_type":
        payload["relationship_type"] = "replaces"
        payload["provision_effect"] = "replaces"
        payload["target_provisions"] = []
        target.source_group_id = repo.get_group.return_value.id
    elif change == "scope":
        payload["target_provisions"] = ["Section 22"]
    else:
        span = payload["supporting_spans"][0]
        start = span["quote"].index("is amended")
        payload["supporting_spans"] = [
            {
                **span,
                "quote": "is amended",
                "char_start": start,
                "char_end": start + len("is amended"),
            }
        ]
    if change == "relationship_type":
        # The old modifying approval cannot declare a new whole-document
        # replacement: a separate explicit replacement review is required.
        with pytest.raises(BadRequestError) as error:
            await stage(service, repo, document, data)
        assert error.value.code == "source_replacement_declaration_required"
        assert not any(
            isinstance(call.args[0], SourceRevisionRelationship) for call in repo.add.call_args_list
        )
        return
    _, created = await stage(service, repo, document, data)
    assert not created.replacement_scope_verified
    assert created.review_provenance["invalidated_reason"] == "proof_identity_changed"
    assert "evidence_hash" not in created.review_provenance
    assert created.review_provenance["reviewer"] == "actual-operator"
