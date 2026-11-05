"""Composition adapter from Knowledge source reads to Retrieval's public port."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import SourcePolicyDeploymentCap
from app.modules.knowledge.source_metadata_read import (
    KnowledgeIncomingModifier,
    KnowledgeSourceMetadataReader,
)
from app.modules.retrieval.source_policy import (
    ModifierExpansionOutcome,
    ModifierExpansionRecord,
    SourceMetadataScope,
)
from app.platform.config.project_ai import SourcePolicyMode, cap_source_policy_mode


class KnowledgeRetrievalSourceMetadataAdapter:
    """Keep Knowledge SQL ownership out of Retrieval module internals."""

    def __init__(self, session: AsyncSession) -> None:
        self._reader = KnowledgeSourceMetadataReader(session)

    supports_period_scope = True

    async def capture(
        self,
        *,
        project_id: uuid.UUID,
        configured_mode: SourcePolicyMode,
        deployment_cap: str,
        as_of: datetime | None,
        generation: int | None = None,
        scoped_document_id: uuid.UUID | None = None,
        period_lookup: bool = False,
        request_scope: dict[str, Any] | None = None,
    ) -> SourceMetadataScope:
        cap = SourcePolicyDeploymentCap(deployment_cap)
        effective_mode = cap_source_policy_mode(configured_mode, cap)
        # A document filter narrows where we search; it does not make an otherwise
        # draft, expired, future, retired, or replaced source applicable.  Keep the
        # same policy join for scoped and unscoped retrieval (including exact recall).
        enforce_join = effective_mode is SourcePolicyMode.ENFORCE
        captured = await self._reader.capture(
            project_id=project_id,
            generation=generation,
            as_of=as_of,
            enforce=enforce_join,
            request_scope=request_scope,
        )
        return SourceMetadataScope(
            selectable=captured.selectable,
            generation=captured.generation,
            configured_mode=configured_mode,
            effective_mode=effective_mode,
            deployment_cap=cap.value,
            reference_date=captured.reference_date.isoformat(),
            explicit_as_of=captured.explicit_as_of,
            exclusion_counts=captured.exclusion_counts,
        )

    async def incoming_modifiers(
        self,
        *,
        project_id: uuid.UUID,
        base_revision_ids: tuple[uuid.UUID, ...],
        generation: int,
        as_of: datetime | None,
        index_build_id: uuid.UUID,
        request_scope: dict[str, Any] | None = None,
    ) -> list[ModifierExpansionRecord]:
        # Probe one additional frontier to retain the identities beyond the two-hop ceiling.
        records: list[KnowledgeIncomingModifier] = []
        frontier = base_revision_ids
        seen = set(base_revision_ids)
        depths = {}
        for depth in range(1, 4):
            if not frontier:
                break
            discovered = await self._reader.incoming_modifiers(
                project_id=project_id,
                base_revision_ids=frontier,
                generation=generation,
                as_of=as_of,
                index_build_id=index_build_id,
                request_scope=request_scope,
            )
            next_frontier = []
            for item in discovered:
                if item.relationship_id in depths:
                    continue
                depths[item.relationship_id] = depth
                outcome = item.outcome
                if outcome == "expanded":
                    if item.modifier_revision_id in seen:
                        outcome = "already_in_recall"
                    elif depth > 2:
                        outcome = "depth_limit"
                    elif len(seen - set(base_revision_ids)) >= 8:
                        outcome = "source_cap_exceeded"
                    elif len(records) >= 20:
                        outcome = "relationship_limit"
                    else:
                        seen.add(item.modifier_revision_id)
                        next_frontier.append(item.modifier_revision_id)
                records.append(replace(item, outcome=outcome))
            frontier = tuple(next_frontier)
        return [
            ModifierExpansionRecord(
                relationship_id=item.relationship_id,
                base_revision_id=item.base_revision_id,
                base_document_id=item.base_document_id,
                modifier_revision_id=item.modifier_revision_id,
                modifier_document_id=item.modifier_document_id,
                modifier_effective_from=item.modifier_effective_from,
                modifier_effective_to=item.modifier_effective_to,
                modifier_published_date=item.modifier_published_date,
                modifier_revision_number=item.modifier_revision_number,
                base_effective_from=item.base_effective_from,
                base_effective_to=item.base_effective_to,
                outcome=ModifierExpansionOutcome(item.outcome),
                target_provisions=item.target_provisions,
                provision_effect=item.provision_effect,
                replacement_scope_verified=item.replacement_scope_verified,
                supporting_spans=item.supporting_spans,
                scope_hash=item.scope_hash,
                applicable=item.applicable,
                depth=depths[item.relationship_id],
            )
            for item in records
        ]
