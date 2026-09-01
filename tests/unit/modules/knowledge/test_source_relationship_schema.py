"""Scoped source-relationship contract tests."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.modules.knowledge.schemas.source_metadata import SourceRelationshipCreate

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
