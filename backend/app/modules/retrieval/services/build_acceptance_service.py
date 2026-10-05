"""Authenticated project-scoped append/read operations for quality receipts."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.index_acceptance import IndexAcceptance, IndexAcceptanceSet
from app.models.index_build import IndexBuildState
from app.modules.retrieval.build_acceptance import (
    AcceptanceSetDefinition,
    BuildAcceptanceArtifact,
    current_acceptance_identity,
    digest,
    semantic_snapshot_hash,
    validate_acceptance,
    validate_definition,
)
from app.modules.retrieval.repositories.index_acceptance_repository import IndexAcceptanceRepository
from app.modules.retrieval.repositories.index_build_repository import IndexBuildRepository
from app.platform.audit.contracts import AuditActorType, AuditEventType, AuditOutcome, AuditRecorder
from app.platform.jobs.errors import PermanentJobError


class BuildAcceptanceService:
    def __init__(self, session: AsyncSession, project_id: uuid.UUID) -> None:
        self.session = session
        self.project_id = project_id
        self.repository = IndexAcceptanceRepository(session, project_id)

    async def list(self, build_id: uuid.UUID) -> list[IndexAcceptance]:
        return await self.repository.list(build_id)

    async def identity(self, build_id: uuid.UUID) -> dict[str, Any]:
        build = await IndexBuildRepository(self.session, self.project_id).get_by_id(build_id)
        if build is None:
            raise NotFoundError("Index build not found.", code="index_build_not_found")
        identity = await current_acceptance_identity(self.session, self.project_id)
        from app.modules.retrieval.build_acceptance import embedding_identity

        return {
            **identity,
            "build_id": build.id,
            "index_configuration_hash": build.configuration_hash,
            "corpus_fingerprint": build.corpus_fingerprint,
            "build_manifest_hash": digest(build.manifest),
            "semantic_structure_hash": await semantic_snapshot_hash(
                self.session, self.project_id, build
            ),
            "embedding_identity": embedding_identity(build),
        }

    async def define(
        self,
        build_id: uuid.UUID,
        definition: AcceptanceSetDefinition,
        *,
        actor_id: str,
        audit: AuditRecorder,
    ) -> IndexAcceptanceSet:
        build = await IndexBuildRepository(self.session, self.project_id).get_by_id(
            build_id, for_update=True
        )
        if build is None:
            raise NotFoundError("Index build not found.", code="index_build_not_found")
        try:
            validate_definition(definition, build)
        except PermanentJobError as exc:
            raise BadRequestError(exc.message, code=exc.code) from exc
        payload = definition.model_dump(mode="json")
        value_hash = digest(payload)
        existing = await self.repository.definition(build_id)
        if existing is not None:
            if existing.definition_hash != value_hash:
                raise BadRequestError(
                    "Approved definition is immutable.", code="index_acceptance_set_immutable"
                )
            return existing
        if build.state not in {IndexBuildState.VALIDATED, IndexBuildState.RETAINED}:
            raise BadRequestError(
                "Only sealed builds may receive a definition.", code="index_build_not_activatable"
            )
        row = IndexAcceptanceSet(
            id=uuid.uuid4(),
            project_id=self.project_id,
            build_id=build_id,
            definition=payload,
            definition_hash=value_hash,
            created_by=actor_id,
        )
        self.repository.add(row)
        audit.record(
            event_type=AuditEventType.INDEX_BUILD_ACCEPTED,
            actor_type=AuditActorType.OPERATOR,
            actor_id=actor_id,
            resource_type="index_acceptance_set",
            resource_id=row.id,
            outcome=AuditOutcome.SUCCESS,
            detail={"build_id": str(build_id), "definition_hash": value_hash},
        )
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def attest(
        self,
        build_id: uuid.UUID,
        artifact: BuildAcceptanceArtifact,
        *,
        actor_id: str,
        audit: AuditRecorder,
    ) -> IndexAcceptance:
        build = await IndexBuildRepository(self.session, self.project_id).get_by_id(
            build_id, for_update=True
        )
        if build is None:
            raise NotFoundError("Index build not found.", code="index_build_not_found")
        if build.state not in {IndexBuildState.VALIDATED, IndexBuildState.RETAINED}:
            raise BadRequestError(
                "Only sealed builds may receive acceptance.", code="index_build_not_activatable"
            )
        try:
            definition = await self.repository.definition(build_id)
            if definition is None or definition.definition_hash != digest(definition.definition):
                raise PermanentJobError(
                    "Approved project/build set is required.", code="index_acceptance_set_missing"
                )
            identity = await current_acceptance_identity(self.session, self.project_id)
            identity["acceptance_definition"] = AcceptanceSetDefinition.model_validate(
                definition.definition
            )
            identity["structure_hash"] = await semantic_snapshot_hash(
                self.session, self.project_id, build
            )
            report = (
                await self.repository.observed_report(artifact.observed_report_id, build_id)
                if artifact.certification == "production"
                else None
            )
            validate_acceptance(artifact, build, **identity, observed_report=report)
        except PermanentJobError as exc:
            raise BadRequestError(exc.message, code=exc.code) from exc
        payload = artifact.model_dump(mode="json")
        artifact_hash = digest(payload)
        existing = await self.repository.by_hash(artifact_hash)
        if existing is not None:
            return existing
        row = IndexAcceptance(
            id=uuid.uuid4(),
            project_id=self.project_id,
            build_id=build_id,
            artifact=payload,
            artifact_hash=artifact_hash,
            created_by=actor_id,
        )
        self.repository.add(row)
        audit.record(
            event_type=AuditEventType.INDEX_BUILD_ACCEPTED,
            actor_type=AuditActorType.OPERATOR,
            actor_id=actor_id,
            resource_type="index_acceptance",
            resource_id=row.id,
            outcome=AuditOutcome.SUCCESS,
            detail={"build_id": str(build_id), "artifact_hash": artifact_hash},
        )
        await self.session.commit()
        await self.session.refresh(row)
        return row
