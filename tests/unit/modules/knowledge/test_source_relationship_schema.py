"""Scoped source-relationship contract tests."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.modules.knowledge.schemas.source_metadata import (
    SourceRelationshipCreate,
    SourceRevisionCreate,
)
from app.modules.knowledge.services.source_metadata_service import _has_modification_cycle

pytestmark = pytest.mark.unit


def test_modifies_relationship_accepts_exact_target_provisions() -> None:
    relationship = SourceRelationshipCreate(
        relationship_type="modifies",
        target_revision_id=uuid.uuid4(),
        target_provisions=["Section 21 — Investment Rebate Rate"],
    )
    assert relationship.target_provisions == ["Section 21 — Investment Rebate Rate"]


def test_replaces_relationship_rejects_provision_scope() -> None:
    with pytest.raises(ValidationError, match="only for modifies"):
        SourceRelationshipCreate(
            relationship_type="replaces",
            target_revision_id=uuid.uuid4(),
            target_provisions=["Section 21"],
        )


@pytest.mark.parametrize("kinds", [("modifies", "modifies"), ("replaces", "modifies")])
def test_duplicate_or_contradictory_target_rejected(kinds):
    target = uuid.uuid4()
    with pytest.raises(ValidationError):
        SourceRevisionCreate(
            relationships=[
                {"relationship_type": kind, "target_revision_id": target} for kind in kinds
            ]
        )


def test_one_replacement_with_many_modifications_is_valid():
    revision = SourceRevisionCreate(
        relationships=[
            {"relationship_type": kind, "target_revision_id": uuid.uuid4()}
            for kind in ["replaces", "modifies", "modifies"]
        ]
    )
    assert len(revision.relationships) == 3


def test_multiple_replacement_histories_are_not_a_merge_operation():
    with pytest.raises(ValidationError, match="only one"):
        SourceRevisionCreate(
            relationships=[
                {"relationship_type": "replaces", "target_revision_id": uuid.uuid4()}
                for _ in range(2)
            ]
        )


def test_graph_allows_fan_out_fan_in_and_chains_but_rejects_reverse_cycles():
    a, b, c, d = [uuid.uuid4() for _ in range(4)]
    graph = {a: {b, c}, b: {d}, c: {d}}
    assert not _has_modification_cycle(graph, a)
    graph[d] = {a}
    assert _has_modification_cycle(graph, d)
    assert _has_modification_cycle(graph, a)
    # An unrelated existing bad component does not prevent correcting this source.
    assert not _has_modification_cycle(graph, uuid.uuid4())
