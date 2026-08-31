"""Public read-only source metadata contract implemented by Knowledge."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import and_, case, exists, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.selectable import FromClause

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.chunk_keyword_index import ChunkKeywordIndex
from app.models.document import Document
from app.models.project import Project
from app.models.source_metadata import (
    SourceActivationEvent,
    SourceLifecycleStatus,
    SourceMetadataRevision,
    SourceRelationshipType,
    SourceRevisionRelationship,
)


@dataclass(frozen=True, slots=True)
class KnowledgeSourceMetadataScope:
    """Knowledge-owned canonical SQL scope plus immutable capture facts."""

    selectable: FromClause
    generation: int
    reference_date: date
    explicit_as_of: datetime | None
    exclusion_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class KnowledgeIncomingModifier:
    """Knowledge-owned governance decision for one incoming MODIFIES edge."""

    relationship_id: uuid.UUID
    base_revision_id: uuid.UUID
    base_document_id: uuid.UUID
    modifier_revision_id: uuid.UUID
    modifier_document_id: uuid.UUID
    modifier_effective_from: str | None
    modifier_effective_to: str | None
    modifier_published_date: str | None
    modifier_revision_number: int | None
    base_effective_from: str | None
    base_effective_to: str | None
    outcome: str


class KnowledgeSourceMetadataReader:
    """Capture source state and build the one selectable used by all retrieval paths."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def capture(
        self,
        *,
        project_id: uuid.UUID,
        generation: int | None,
        as_of: datetime | None,
        enforce: bool,
    ) -> KnowledgeSourceMetadataScope:
        current_generation = await self._session.scalar(
            select(Project.source_metadata_generation).where(
                Project.id == project_id,
                Project.deleted_at.is_(None),
            )
        )
        if current_generation is None:
            raise NotFoundError(message="Project not found.", code="project_not_found")
        resolved_generation = current_generation if generation is None else generation
        if resolved_generation < 0 or resolved_generation > current_generation:
            raise BadRequestError(
                message="The source metadata generation is outside the Project history.",
                code="source_generation_invalid",
            )

        reference_date = as_of.date() if as_of is not None else datetime.now(UTC).date()
        base = _canonical_source_scope(
            project_id=project_id,
            generation=resolved_generation,
            reference_date=reference_date,
            historical=as_of is not None,
        )
        decision_rows = await self._session.execute(
            select(
                base.c.source_policy_exclusion_reason,
                func.count(base.c.source_document_id),
            )
            .where(base.c.source_policy_applicable.is_(False))
            .group_by(base.c.source_policy_exclusion_reason)
        )
        exclusion_counts = {
            str(reason or "not_applicable"): int(count) for reason, count in decision_rows.all()
        }
        selectable = (
            select(*base.c)
            .where(base.c.source_policy_applicable.is_(True))
            .subquery("canonical_source_scope_enforced")
            if enforce
            else base
        )
        return KnowledgeSourceMetadataScope(
            selectable=selectable,
            generation=resolved_generation,
            reference_date=reference_date,
            explicit_as_of=as_of,
            exclusion_counts=exclusion_counts,
        )

    async def incoming_modifiers(
        self,
        *,
        project_id: uuid.UUID,
        base_revision_ids: tuple[uuid.UUID, ...],
        generation: int,
        as_of: datetime | None,
        index_build_id: uuid.UUID,
    ) -> list[KnowledgeIncomingModifier]:
        """Resolve depth-one incoming MODIFIES edges under one captured snapshot."""
        if not base_revision_ids:
            return []
        modifier = aliased(SourceMetadataRevision, name="modifier_revision")
        base = aliased(SourceMetadataRevision, name="base_revision")
        edge_rows = (
            await self._session.execute(
                select(
                    SourceRevisionRelationship.id.label("relationship_id"),
                    SourceRevisionRelationship.project_id.label("relationship_project_id"),
                    SourceRevisionRelationship.source_revision_id.label("modifier_revision_id"),
                    SourceRevisionRelationship.target_revision_id.label("base_revision_id"),
                    modifier.project_id.label("modifier_project_id"),
                    modifier.document_id.label("modifier_document_id"),
                    modifier.source_group_id.label("modifier_source_group_id"),
                    modifier.revision_number.label("modifier_revision_number"),
                    modifier.revision_label.label("modifier_revision_label"),
                    modifier.title.label("modifier_title"),
                    modifier.published_date.label("modifier_published_date"),
                    modifier.effective_from.label("modifier_effective_from"),
                    modifier.effective_to.label("modifier_effective_to"),
                    modifier.lifecycle_status.label("modifier_lifecycle_status"),
                    base.project_id.label("base_project_id"),
                    base.document_id.label("base_document_id"),
                    base.effective_from.label("base_effective_from"),
                    base.effective_to.label("base_effective_to"),
                )
                .join(modifier, modifier.id == SourceRevisionRelationship.source_revision_id)
                .join(base, base.id == SourceRevisionRelationship.target_revision_id)
                .where(
                    SourceRevisionRelationship.relationship_type == SourceRelationshipType.MODIFIES,
                    SourceRevisionRelationship.target_revision_id.in_(base_revision_ids),
                )
            )
        ).all()
        if not edge_rows:
            return []

        modifier_document_ids = tuple(
            {row.modifier_document_id for row in edge_rows if row.modifier_document_id is not None}
        )
        reference_date = (as_of or datetime.now(UTC)).date()
        activation_revision = aliased(
            SourceMetadataRevision,
            name="modifier_activation_revision",
        )
        activation_candidates = (
            select(
                SourceActivationEvent.document_id,
                SourceActivationEvent.source_revision_id,
                func.row_number()
                .over(
                    partition_by=SourceActivationEvent.document_id,
                    order_by=(
                        SourceActivationEvent.generation.desc(),
                        SourceActivationEvent.created_at.desc(),
                        SourceActivationEvent.id.desc(),
                    ),
                )
                .label("position"),
            )
            .where(
                SourceActivationEvent.project_id == project_id,
                SourceActivationEvent.generation <= generation,
                SourceActivationEvent.document_id.in_(modifier_document_ids),
            )
            .join(
                activation_revision,
                and_(
                    activation_revision.id == SourceActivationEvent.source_revision_id,
                    activation_revision.project_id == project_id,
                ),
            )
        )
        if as_of is not None:
            activation_candidates = activation_candidates.where(
                activation_revision.lifecycle_status.in_(
                    [SourceLifecycleStatus.ACTIVE, SourceLifecycleStatus.RETIRED]
                ),
                or_(
                    activation_revision.effective_from.is_(None),
                    activation_revision.effective_from <= reference_date,
                ),
                or_(
                    activation_revision.effective_to.is_(None),
                    activation_revision.effective_to >= reference_date,
                ),
            )
        activation_rank = activation_candidates.cte("modifier_activation_rank")
        activation_rows = await self._session.execute(
            select(
                activation_rank.c.document_id,
                activation_rank.c.source_revision_id,
            ).where(activation_rank.c.position == 1)
        )
        active_revision_by_document = {
            row.document_id: row.source_revision_id for row in activation_rows.all()
        }
        indexed_rows = await self._session.execute(
            select(ChunkKeywordIndex.document_id)
            .where(
                ChunkKeywordIndex.project_id == project_id,
                ChunkKeywordIndex.index_build_id == index_build_id,
                ChunkKeywordIndex.document_id.in_(modifier_document_ids),
            )
            .distinct()
        )
        indexed_documents = set(indexed_rows.scalars().all())

        records: list[KnowledgeIncomingModifier] = []
        for row in edge_rows:
            selected_revision = active_revision_by_document.get(row.modifier_document_id)
            outcome = _modifier_outcome(
                row,
                project_id=project_id,
                selected_revision=selected_revision,
                indexed=row.modifier_document_id in indexed_documents,
                reference_date=reference_date,
            )
            records.append(
                KnowledgeIncomingModifier(
                    relationship_id=row.relationship_id,
                    base_revision_id=row.base_revision_id,
                    base_document_id=row.base_document_id,
                    modifier_revision_id=row.modifier_revision_id,
                    modifier_document_id=row.modifier_document_id,
                    modifier_effective_from=_isoformat(row.modifier_effective_from),
                    modifier_effective_to=_isoformat(row.modifier_effective_to),
                    modifier_published_date=_isoformat(row.modifier_published_date),
                    modifier_revision_number=row.modifier_revision_number,
                    base_effective_from=_isoformat(row.base_effective_from),
                    base_effective_to=_isoformat(row.base_effective_to),
                    outcome=outcome,
                )
            )
        return records


def _isoformat(value: object) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _modifier_outcome(
    row: Any,
    *,
    project_id: uuid.UUID,
    selected_revision: uuid.UUID | None,
    indexed: bool,
    reference_date: date,
) -> str:
    """Return the single fail-closed governance outcome for an incoming edge."""
    if not (
        row.relationship_project_id == project_id
        and row.modifier_project_id == project_id
        and row.base_project_id == project_id
    ):
        return "cross_project_or_generation"
    required_metadata = (
        row.modifier_document_id,
        row.modifier_source_group_id,
        row.modifier_revision_number,
        str(row.modifier_revision_label or "").strip(),
        str(row.modifier_title or "").strip(),
        row.modifier_lifecycle_status,
        row.modifier_effective_from,
    )
    if not all(required_metadata):
        return "ungoverned_or_incomplete_metadata"
    if row.modifier_lifecycle_status != SourceLifecycleStatus.ACTIVE:
        return "inactive"
    effective_from = row.modifier_effective_from
    effective_to = row.modifier_effective_to
    if effective_from > reference_date or (
        effective_to is not None and effective_to < reference_date
    ):
        return "outside_as_of"
    if selected_revision is None:
        return "ungoverned_or_incomplete_metadata"
    if selected_revision != row.modifier_revision_id:
        return "stale_or_replaced_revision"
    if not indexed:
        return "not_in_active_index"
    return "expanded"


def _canonical_source_scope(
    *,
    project_id: uuid.UUID,
    generation: int,
    reference_date: date,
    historical: bool,
) -> FromClause:
    revision_interval_applies = and_(
        or_(
            SourceMetadataRevision.effective_from.is_(None),
            SourceMetadataRevision.effective_from <= reference_date,
        ),
        or_(
            SourceMetadataRevision.effective_to.is_(None),
            SourceMetadataRevision.effective_to >= reference_date,
        ),
    )
    governed_documents = (
        select(SourceActivationEvent.document_id)
        .join(
            SourceMetadataRevision,
            and_(
                SourceMetadataRevision.id == SourceActivationEvent.source_revision_id,
                SourceMetadataRevision.project_id == project_id,
            ),
        )
        .where(
            SourceActivationEvent.project_id == project_id,
            SourceActivationEvent.generation <= generation,
            SourceMetadataRevision.lifecycle_status != SourceLifecycleStatus.UNSPECIFIED,
        )
        .distinct()
        .cte("governed_source_documents")
    )
    has_governed_revision = exists(
        select(literal(1)).where(
            governed_documents.c.document_id == SourceActivationEvent.document_id
        )
    )
    activation_candidates = (
        select(
            SourceActivationEvent.document_id.label("document_id"),
            SourceActivationEvent.source_revision_id.label("source_revision_id"),
            SourceMetadataRevision.source_group_id,
            SourceMetadataRevision.title,
            SourceMetadataRevision.source_type,
            SourceMetadataRevision.revision_number,
            SourceMetadataRevision.revision_label,
            SourceMetadataRevision.published_date,
            SourceMetadataRevision.effective_from,
            SourceMetadataRevision.effective_to,
            SourceMetadataRevision.lifecycle_status,
            SourceMetadataRevision.source_role,
            func.row_number()
            .over(
                partition_by=SourceActivationEvent.document_id,
                order_by=(
                    SourceActivationEvent.generation.desc(),
                    SourceActivationEvent.created_at.desc(),
                    SourceActivationEvent.id.desc(),
                ),
            )
            .label("position"),
        )
        .where(
            SourceActivationEvent.project_id == project_id,
            SourceActivationEvent.generation <= generation,
        )
        .join(
            SourceMetadataRevision,
            and_(
                SourceMetadataRevision.id == SourceActivationEvent.source_revision_id,
                SourceMetadataRevision.project_id == project_id,
            ),
        )
    )
    if historical:
        activation_candidates = activation_candidates.where(
            or_(
                and_(
                    SourceMetadataRevision.lifecycle_status == SourceLifecycleStatus.UNSPECIFIED,
                    ~has_governed_revision,
                ),
                and_(
                    SourceMetadataRevision.lifecycle_status.in_(
                        [SourceLifecycleStatus.ACTIVE, SourceLifecycleStatus.RETIRED]
                    ),
                    revision_interval_applies,
                ),
            )
        )
    ranked_activations = activation_candidates.cte("ranked_source_activations")
    state = (
        select(
            ranked_activations.c.document_id,
            ranked_activations.c.source_revision_id,
            ranked_activations.c.source_group_id,
            ranked_activations.c.title,
            ranked_activations.c.source_type,
            ranked_activations.c.revision_number,
            ranked_activations.c.revision_label,
            ranked_activations.c.published_date,
            ranked_activations.c.effective_from,
            ranked_activations.c.effective_to,
            ranked_activations.c.lifecycle_status,
            ranked_activations.c.source_role,
        )
        .where(ranked_activations.c.position == 1)
        .cte("active_source_state")
    )
    relationships = (
        select(
            SourceRevisionRelationship.source_revision_id,
            func.jsonb_agg(
                func.jsonb_build_object(
                    "id",
                    SourceRevisionRelationship.id,
                    "relationship_type",
                    SourceRevisionRelationship.relationship_type,
                    "target_revision_id",
                    SourceRevisionRelationship.target_revision_id,
                )
            ).label("relationships"),
        )
        .where(SourceRevisionRelationship.project_id == project_id)
        .group_by(SourceRevisionRelationship.source_revision_id)
        .cte("source_relationship_aggregate")
    )

    interval_applies = and_(
        or_(state.c.effective_from.is_(None), state.c.effective_from <= reference_date),
        or_(state.c.effective_to.is_(None), state.c.effective_to >= reference_date),
    )
    applicable_replacements = (
        select(SourceRevisionRelationship.target_revision_id)
        .join(
            state,
            state.c.source_revision_id == SourceRevisionRelationship.source_revision_id,
        )
        .where(
            SourceRevisionRelationship.project_id == project_id,
            SourceRevisionRelationship.relationship_type == SourceRelationshipType.REPLACES,
            state.c.lifecycle_status == SourceLifecycleStatus.ACTIVE,
            interval_applies,
        )
        .cte("applicable_source_replacements")
    )
    has_replacement = exists(
        select(literal(1)).where(
            applicable_replacements.c.target_revision_id == state.c.source_revision_id
        )
    )

    # A governed document can have no candidate state for a historical date (for
    # example, its first active revision starts in the future).  The outer join
    # below must not turn that absence into legacy/neutral applicability.
    document_has_governed_revision = exists(
        select(literal(1)).where(governed_documents.c.document_id == Document.id)
    )
    neutral = or_(
        state.c.lifecycle_status == SourceLifecycleStatus.UNSPECIFIED,
        and_(
            state.c.source_revision_id.is_(None),
            ~document_has_governed_revision,
        ),
    )
    not_draft = state.c.lifecycle_status != SourceLifecycleStatus.DRAFT
    if historical:
        applicable = func.coalesce(
            or_(
                neutral,
                and_(
                    not_draft,
                    state.c.lifecycle_status.in_(
                        [SourceLifecycleStatus.ACTIVE, SourceLifecycleStatus.RETIRED]
                    ),
                    interval_applies,
                ),
            ),
            literal(False),
        )
    else:
        applicable = func.coalesce(
            or_(
                neutral,
                and_(state.c.lifecycle_status == SourceLifecycleStatus.ACTIVE, interval_applies),
                and_(
                    state.c.lifecycle_status == SourceLifecycleStatus.RETIRED,
                    interval_applies,
                    ~has_replacement,
                ),
            ),
            literal(False),
        )

    exclusion_reason = case(
        (applicable, None),
        (state.c.lifecycle_status == SourceLifecycleStatus.DRAFT, "draft"),
        (~interval_applies, "outside_effective_interval"),
        (
            and_(
                state.c.lifecycle_status == SourceLifecycleStatus.RETIRED,
                has_replacement,
                literal(not historical),
            ),
            "retired_replaced",
        ),
        else_="not_applicable",
    )
    return (
        select(
            Document.id.label("source_document_id"),
            state.c.source_revision_id,
            state.c.source_group_id,
            state.c.title.label("source_title"),
            state.c.source_type.label("source_type"),
            state.c.revision_number.label("source_revision_number"),
            state.c.revision_label.label("source_revision_label"),
            state.c.published_date.label("source_published_date"),
            state.c.effective_from.label("source_effective_from"),
            state.c.effective_to.label("source_effective_to"),
            state.c.lifecycle_status.label("source_lifecycle_status"),
            state.c.source_role.label("source_role"),
            relationships.c.relationships.label("source_relationships"),
            applicable.label("source_policy_applicable"),
            exclusion_reason.label("source_policy_exclusion_reason"),
        )
        .select_from(Document)
        .outerjoin(
            state,
            state.c.document_id == Document.id,
        )
        .outerjoin(
            relationships,
            relationships.c.source_revision_id == state.c.source_revision_id,
        )
        .where(Document.project_id == project_id)
        .subquery("canonical_source_scope")
    )
