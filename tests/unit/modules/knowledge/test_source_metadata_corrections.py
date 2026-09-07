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
