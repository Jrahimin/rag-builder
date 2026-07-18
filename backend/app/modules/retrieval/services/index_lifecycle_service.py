"""Safe corpus build submission and atomic pointer transitions."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.index_build import IndexBuild, IndexBuildOperation, IndexBuildState
from app.modules.retrieval.repositories.index_build_repository import IndexBuildRepository
from app.modules.retrieval.schemas.index_lifecycle import LifecycleJobResponse
from app.modules.retrieval.workflows.index_build_workflow import activate_index_build
from app.platform.audit.contracts import AuditActorType, AuditEventType, AuditOutcome, AuditRecorder
from app.platform.jobs.contracts import (
    DurableJobSubmitter,
    JobConfiguration,
    JobDefinition,
    RetryPolicy,
)
from app.platform.jobs.names import CORPUS_REEMBED, CORPUS_REINDEX, STORAGE_RECONCILE


class IndexLifecycleService:
    """Own index-build creation, inspection, activation, and rollback."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        job_submitter: DurableJobSubmitter,
        job_configuration: JobConfiguration,
        *,
        embedding_set_version: int,
        job_max_attempts: int,
        audit: AuditRecorder,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._jobs = job_submitter
        self._configuration = job_configuration
        self._embedding_set_version = embedding_set_version
        self._job_max_attempts = job_max_attempts
        self._audit = audit
        self._repository = IndexBuildRepository(session, project_id)

    async def enqueue_reembed(self, *, auto_activate: bool = False) -> LifecycleJobResponse:
        return await self._enqueue(IndexBuildOperation.REEMBED, CORPUS_REEMBED, auto_activate)

    async def enqueue_reindex(self, *, auto_activate: bool = False) -> LifecycleJobResponse:
        return await self._enqueue(IndexBuildOperation.REINDEX, CORPUS_REINDEX, auto_activate)

    async def enqueue_storage_reconciliation(self) -> LifecycleJobResponse:
        submission = await self._jobs.stage(
            JobDefinition(
                name=STORAGE_RECONCILE,
                project_id=self._project_id,
                payload={},
                idempotency_key=f"storage.reconcile:{self._project_id}:{uuid.uuid4()}",
                retry=RetryPolicy(max_attempts=self._job_max_attempts),
            ),
            self._configuration,
        )
        self._audit.record(
            event_type=AuditEventType.STORAGE_RECONCILIATION_REQUESTED,
            actor_type=AuditActorType.OPERATOR,
            resource_type="job_run",
            resource_id=submission.job_id,
            outcome=AuditOutcome.SUCCESS,
        )
        await self._session.commit()
        await self._jobs.dispatch(submission.job_id)
        return LifecycleJobResponse(job_id=submission.job_id, created=submission.created)

    async def _enqueue(
        self, operation: IndexBuildOperation, job_name: str, auto_activate: bool
    ) -> LifecycleJobResponse:
        build = IndexBuild(
            project_id=self._project_id,
            operation=operation,
            state=IndexBuildState.BUILDING,
            embedding_set_version=self._embedding_set_version,
            configuration_hash=self._configuration.digest(),
        )
        self._repository.add(build)
        await self._repository.flush()
        submission = await self._jobs.stage(
            JobDefinition(
                name=job_name,
                project_id=self._project_id,
                payload={"build_id": str(build.id), "auto_activate": auto_activate},
                idempotency_key=f"{job_name}:{self._project_id}:{build.id}",
                retry=RetryPolicy(max_attempts=self._job_max_attempts),
            ),
            self._configuration,
        )
        build.job_id = submission.job_id
        await self._session.commit()
        await self._jobs.dispatch(submission.job_id)
        return LifecycleJobResponse(
            job_id=submission.job_id, build_id=build.id, created=submission.created
        )

    async def list(self) -> tuple[list[IndexBuild], uuid.UUID | None, uuid.UUID | None]:
        pointer = await self._repository.get_pointer()
        return (
            await self._repository.list_recent(),
            pointer.active_build_id if pointer else None,
            pointer.previous_build_id if pointer else None,
        )

    async def get(self, build_id: uuid.UUID) -> IndexBuild:
        build = await self._repository.get_by_id(build_id)
        if build is None:
            raise NotFoundError(message="Index build not found.", code="index_build_not_found")
        return build

    async def activate(self, build_id: uuid.UUID) -> IndexBuild:
        build = await self.get(build_id)
        if (
            build.state not in {IndexBuildState.VALIDATED, IndexBuildState.RETAINED}
            or build.validated_at is None
            or build.corpus_fingerprint is None
            or build.vector_count != build.chunk_count
            or build.keyword_count != build.chunk_count
        ):
            raise BadRequestError(
                message="Only a validated or retained build can be activated.",
                code="index_build_not_activatable",
            )
        await activate_index_build(self._session, self._project_id, build)
        self._record(AuditEventType.INDEX_BUILD_ACTIVATED, build)
        await self._session.commit()
        await self._session.refresh(build)
        return build

    async def rollback(self) -> IndexBuild:
        pointer = await self._repository.get_pointer(for_update=True)
        if pointer is None or pointer.previous_build_id is None:
            raise BadRequestError(
                message="No verified retained build is available for rollback.",
                code="index_rollback_unavailable",
            )
        target = await self.get(pointer.previous_build_id)
        if target.state is not IndexBuildState.RETAINED or target.validated_at is None:
            raise BadRequestError(
                message="The rollback target is not a verified retained build.",
                code="index_rollback_target_invalid",
            )
        await activate_index_build(self._session, self._project_id, target)
        self._record(AuditEventType.INDEX_BUILD_ROLLED_BACK, target)
        await self._session.commit()
        await self._session.refresh(target)
        return target

    def _record(self, event_type: AuditEventType, build: IndexBuild) -> None:
        self._audit.record(
            event_type=event_type,
            actor_type=AuditActorType.OPERATOR,
            resource_type="index_build",
            resource_id=build.id,
            outcome=AuditOutcome.SUCCESS,
            detail={"operation": build.operation.value},
        )
