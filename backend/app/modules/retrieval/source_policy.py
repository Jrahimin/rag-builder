"""Read-only source-policy seam and post-ranking source consolidation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy.sql.selectable import FromClause

from app.modules.retrieval.retrievers.models import CandidateHit
from app.platform.config.project_ai import SourcePolicyMode

SOURCE_METADATA_COLUMNS = (
    "source_document_id",
    "source_revision_id",
    "source_group_id",
    "source_title",
    "source_type",
    "source_revision_number",
    "source_revision_label",
    "source_published_date",
    "source_effective_from",
    "source_effective_to",
    "source_lifecycle_status",
    "source_role",
    "source_relationships",
    "source_policy_applicable",
    "source_policy_exclusion_reason",
)


class ModifierExpansionOutcome(StrEnum):
    """One terminal inclusion/exclusion outcome for an incoming MODIFIES edge."""

    EXPANDED = "expanded"
    INACTIVE = "inactive"
    OUTSIDE_AS_OF = "outside_as_of"
    NOT_IN_ACTIVE_INDEX = "not_in_active_index"
    CROSS_PROJECT_OR_GENERATION = "cross_project_or_generation"
    STALE_OR_REPLACED_REVISION = "stale_or_replaced_revision"
    UNGOVERNED_OR_INCOMPLETE_METADATA = "ungoverned_or_incomplete_metadata"
    DUPLICATE = "duplicate"
    CYCLE = "cycle"
    SOURCE_CAP_EXCEEDED = "source_cap_exceeded"
    CANDIDATE_CAP_EXCEEDED = "candidate_cap_exceeded"


@dataclass(frozen=True, slots=True)
class ModifierExpansionRecord:
    """Sanitized depth-one relationship decision and recall provenance."""

    relationship_id: uuid.UUID
    base_revision_id: uuid.UUID
    base_document_id: uuid.UUID
    modifier_revision_id: uuid.UUID
    modifier_document_id: uuid.UUID
    modifier_effective_from: str | None
    modifier_published_date: str | None
    modifier_revision_number: int | None
    outcome: ModifierExpansionOutcome
    base_effective_from: str | None = None
    base_effective_to: str | None = None
    modifier_effective_to: str | None = None
    candidate_count: int = 0
    retained_candidate_count: int = 0

    def diagnostic(self) -> dict[str, Any]:
        return {
            "relationship_id": str(self.relationship_id),
            "relationship_type": "modifies",
            "depth": 1,
            "base_revision_id": str(self.base_revision_id),
            "base_document_id": str(self.base_document_id),
            "modifier_revision_id": str(self.modifier_revision_id),
            "modifier_document_id": str(self.modifier_document_id),
            "base_effective_from": self.base_effective_from,
            "base_effective_to": self.base_effective_to,
            "modifier_effective_from": self.modifier_effective_from,
            "modifier_effective_to": self.modifier_effective_to,
            "modifier_published_date": self.modifier_published_date,
            "modifier_revision_number": self.modifier_revision_number,
            "outcome": self.outcome.value,
            "candidate_count": self.candidate_count,
            "retained_candidate_count": self.retained_candidate_count,
        }

    def recall_provenance(self) -> dict[str, Any]:
        """Return relationship facts only; callers must not treat them as grounding trust."""
        return {
            key: value
            for key, value in self.diagnostic().items()
            if key not in {"outcome", "candidate_count", "retained_candidate_count"}
        }


@dataclass(frozen=True, slots=True)
class SourceMetadataScope:
    """One immutable source-state view reused by every retrieval branch."""

    selectable: FromClause | None
    generation: int
    configured_mode: SourcePolicyMode
    effective_mode: SourcePolicyMode
    deployment_cap: str
    reference_date: str
    explicit_as_of: datetime | None
    exclusion_counts: dict[str, int] = field(default_factory=dict)


class SourceMetadataReadPort(Protocol):
    """Knowledge-supplied, read-only source metadata contract."""

    async def capture(
        self,
        *,
        project_id: uuid.UUID,
        configured_mode: SourcePolicyMode,
        deployment_cap: str,
        as_of: datetime | None,
        generation: int | None = None,
    ) -> SourceMetadataScope: ...

    async def incoming_modifiers(
        self,
        *,
        project_id: uuid.UUID,
        base_revision_ids: tuple[uuid.UUID, ...],
        generation: int,
        as_of: datetime | None,
        index_build_id: uuid.UUID,
    ) -> list[ModifierExpansionRecord]: ...


@dataclass(frozen=True, slots=True)
class SourcePolicyApplication:
    candidates: list[CandidateHit]
    consolidation_counts: dict[str, int]
    observed_exclusion_counts: dict[str, int]


def source_metadata_from_row(row: Any) -> dict[str, Any]:
    """Extract the stable source contract from a SQLAlchemy result row."""
    mapping = row._mapping if hasattr(row, "_mapping") else row
    metadata: dict[str, Any] = {}
    for name in SOURCE_METADATA_COLUMNS:
        value = mapping.get(name)
        if value is None:
            continue
        if name == "source_relationships":
            metadata[name] = list(value) if isinstance(value, list) else []
        elif hasattr(value, "value"):
            metadata[name] = value.value
        elif hasattr(value, "isoformat"):
            metadata[name] = value.isoformat()
        else:
            metadata[name] = value
    return metadata


def apply_source_policy(
    candidates: list[CandidateHit],
    *,
    mode: SourcePolicyMode,
) -> SourcePolicyApplication:
    """Apply or observe post-ranking role tie-breaking and revision consolidation."""
    if mode is SourcePolicyMode.OFF:
        return SourcePolicyApplication(
            candidates=candidates,
            consolidation_counts={},
            observed_exclusion_counts={},
        )
    hypothetical, consolidation = _enforced_candidates(candidates)
    observed_exclusions: dict[str, int] = {}
    for candidate in candidates:
        if candidate.metadata.get("source_policy_applicable") is False:
            reason = str(
                candidate.metadata.get("source_policy_exclusion_reason") or "not_applicable"
            )
            observed_exclusions[reason] = observed_exclusions.get(reason, 0) + 1

    if mode is not SourcePolicyMode.ENFORCE:
        return SourcePolicyApplication(
            candidates=candidates,
            consolidation_counts=consolidation,
            observed_exclusion_counts=observed_exclusions,
        )
    return SourcePolicyApplication(
        candidates=hypothetical,
        consolidation_counts=consolidation,
        observed_exclusion_counts=observed_exclusions,
    )


def add_retrieval_provenance(
    candidates: list[CandidateHit],
    *,
    index_build_id: uuid.UUID,
    source_scope: SourceMetadataScope,
    configuration_hash: str | None,
    config_provenance: dict[str, Any],
) -> list[CandidateHit]:
    """Attach identical execution identifiers before hydration and citation creation."""
    shared = {
        "index_build_id": str(index_build_id),
        "source_metadata_generation": source_scope.generation,
        "source_policy_mode": source_scope.effective_mode.value,
        "source_policy_configured_mode": source_scope.configured_mode.value,
        "source_policy_deployment_cap": source_scope.deployment_cap,
        "retrieval_reference_date": source_scope.reference_date,
        "configuration_hash": configuration_hash,
        "config_provenance": dict(config_provenance),
    }
    return [
        replace(candidate, metadata={**candidate.metadata, **shared}) for candidate in candidates
    ]


def _enforced_candidates(
    candidates: list[CandidateHit],
) -> tuple[list[CandidateHit], dict[str, int]]:
    applicable = [
        candidate
        for candidate in candidates
        if candidate.metadata.get("source_policy_applicable") is not False
    ]
    ranked = [
        candidate
        for _, candidate in sorted(
            enumerate(applicable),
            key=lambda item: (-item[1].score, item[0]),
        )
    ]
    chosen_revision_by_group: dict[str, str] = {}
    consolidated: list[CandidateHit] = []
    removed = 0
    for candidate in ranked:
        group_id = candidate.metadata.get("source_group_id")
        revision_id = candidate.metadata.get("source_revision_id")
        if group_id is None or revision_id is None:
            consolidated.append(candidate)
            continue
        group_key = str(group_id)
        revision_key = str(revision_id)
        chosen = chosen_revision_by_group.setdefault(group_key, revision_key)
        if chosen != revision_key:
            removed += 1
            continue
        consolidated.append(candidate)
    indexed = list(enumerate(consolidated))
    role_tied = [
        candidate
        for _, candidate in sorted(
            indexed,
            key=lambda item: (
                -item[1].score,
                -_role_priority(item[1].metadata.get("source_role")),
                item[0],
            ),
        )
    ]
    counts = {"same_source_group_lower_ranked_revision": removed} if removed else {}
    return role_tied, counts


def _role_priority(value: object) -> int:
    return {
        "primary": 3,
        "supporting": 2,
        "reference": 1,
        "unspecified": 0,
    }.get(str(value), 0)
