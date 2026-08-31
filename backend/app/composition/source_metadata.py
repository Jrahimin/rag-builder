"""Composition adapter from Knowledge source reads to Retrieval's public port."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import SourcePolicyDeploymentCap
from app.modules.knowledge.source_metadata_read import KnowledgeSourceMetadataReader
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

    async def capture(
        self,
        *,
        project_id: uuid.UUID,
        configured_mode: SourcePolicyMode,
        deployment_cap: str,
        as_of: datetime | None,
        generation: int | None = None,
    ) -> SourceMetadataScope:
        cap = SourcePolicyDeploymentCap(deployment_cap)
        effective_mode = cap_source_policy_mode(configured_mode, cap)
        captured = await self._reader.capture(
            project_id=project_id,
            generation=generation,
            as_of=as_of,
            enforce=effective_mode is SourcePolicyMode.ENFORCE,
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
    ) -> list[ModifierExpansionRecord]:
        records = await self._reader.incoming_modifiers(
            project_id=project_id,
            base_revision_ids=base_revision_ids,
            generation=generation,
            as_of=as_of,
            index_build_id=index_build_id,
        )
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
            )
            for item in records
        ]
