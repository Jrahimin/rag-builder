"""Source-policy ranking, consolidation, and legacy-compatibility tests."""

from __future__ import annotations

import uuid

import pytest

from app.modules.retrieval.retrievers.models import CandidateHit, CandidateSource
from app.modules.retrieval.source_policy import apply_source_policy, source_metadata_from_row
from app.platform.config.project_ai import SourcePolicyMode

pytestmark = pytest.mark.unit


def test_applicable_source_rows_omit_exclusion_reason() -> None:
    metadata = source_metadata_from_row(
        {
            "source_policy_applicable": True,
            "source_policy_exclusion_reason": None,
        }
    )

    assert metadata["source_policy_applicable"] is True
    assert "source_policy_exclusion_reason" not in metadata


def _candidate(
    score: float,
    *,
    revision: str | None = None,
    group: str | None = None,
    role: str = "unspecified",
    applicable: bool = True,
    reason: str | None = None,
) -> CandidateHit:
    metadata: dict[str, object] = {
        "source_role": role,
        "source_policy_applicable": applicable,
    }
    if revision is not None:
        metadata["source_revision_id"] = revision
    if group is not None:
        metadata["source_group_id"] = group
    if reason is not None:
        metadata["source_policy_exclusion_reason"] = reason
    return CandidateHit(
        chunk_id=uuid.uuid4(),
        score=score,
        source=CandidateSource.HYBRID,
        metadata=metadata,
    )


def test_enforce_filters_drafts_and_observe_preserves_ranked_candidates() -> None:
    active = _candidate(0.8)
    draft = _candidate(0.9, applicable=False, reason="draft")

    observed = apply_source_policy([draft, active], mode=SourcePolicyMode.OBSERVE)
    enforced = apply_source_policy([draft, active], mode=SourcePolicyMode.ENFORCE)

    assert observed.candidates == [draft, active]
    assert observed.observed_exclusion_counts == {"draft": 1}
    assert enforced.candidates == [active]


def test_same_group_revisions_consolidate_but_modifying_source_stays_separate() -> None:
    previous = _candidate(0.7, revision="revision-1", group="policy")
    current = _candidate(0.9, revision="revision-2", group="policy")
    modifier = _candidate(0.8, revision="modifier-1", group="amendment")

    result = apply_source_policy(
        [previous, current, modifier],
        mode=SourcePolicyMode.ENFORCE,
    )

    assert result.candidates == [current, modifier]
    assert result.consolidation_counts == {"same_source_group_lower_ranked_revision": 1}


def test_role_breaks_only_equal_scores_and_never_overrides_relevance() -> None:
    lower_primary = _candidate(0.79, role="primary")
    equal_reference = _candidate(0.8, role="reference")
    equal_supporting = _candidate(0.8, role="supporting")

    result = apply_source_policy(
        [equal_reference, lower_primary, equal_supporting],
        mode=SourcePolicyMode.ENFORCE,
    )

    assert result.candidates == [equal_supporting, equal_reference, lower_primary]


def test_role_does_not_choose_the_revision_during_group_consolidation() -> None:
    first_ranked_reference = _candidate(
        0.8,
        revision="revision-1",
        group="policy",
        role="reference",
    )
    equal_primary_revision = _candidate(
        0.8,
        revision="revision-2",
        group="policy",
        role="primary",
    )

    result = apply_source_policy(
        [first_ranked_reference, equal_primary_revision],
        mode=SourcePolicyMode.ENFORCE,
    )

    assert result.candidates == [first_ranked_reference]
    assert result.consolidation_counts == {"same_source_group_lower_ranked_revision": 1}


def test_legacy_candidate_without_source_identity_remains_neutral() -> None:
    legacy = CandidateHit(
        chunk_id=uuid.uuid4(),
        score=0.5,
        source=CandidateSource.SEMANTIC,
        metadata={},
    )

    result = apply_source_policy([legacy], mode=SourcePolicyMode.ENFORCE)

    assert result.candidates == [legacy]
    assert result.consolidation_counts == {}
