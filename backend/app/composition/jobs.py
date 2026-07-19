"""Runtime composition for durable job submission and outbox recovery."""

from __future__ import annotations

import asyncio
import contextlib
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.composition.audit import DatabaseAuditRecorder
from app.composition.webhooks import DatabaseWebhookEventPublisher
from app.core.config import Settings
from app.models.document import DocumentStatus
from app.models.index_build import IndexBuildState
from app.models.project import Project
from app.modules.jobs.services.job_service import JobService
from app.modules.knowledge.repositories.document_repository import DocumentRepository
from app.modules.retrieval.repositories.index_build_repository import IndexBuildRepository
from app.platform.jobs.contracts import JobQueue

logger = structlog.get_logger(__name__)


def build_job_service(
    *,
    session: AsyncSession,
    project_id: uuid.UUID,
    settings: Settings,
    queue: JobQueue,
) -> JobService:
    return JobService(
        session,
        project_id,
        queue,
        settings.jobs,
        audit=DatabaseAuditRecorder(session, project_id),
        webhooks=DatabaseWebhookEventPublisher(session, project_id, settings),
    )


class DurableJobDispatcher:
    """Poll committed outbox rows and recover expired worker leases."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        queue: JobQueue,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._queue = queue
        self._stop = asyncio.Event()

    async def run_forever(self) -> None:
        logger.info("job_dispatcher_started")
        try:
            while not self._stop.is_set():
                try:
                    dispatched = await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("job_dispatcher_iteration_failed")
                    dispatched = 0
                if dispatched == 0:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(
                            self._stop.wait(),
                            timeout=self._settings.jobs.dispatcher_poll_seconds,
                        )
        finally:
            logger.info("job_dispatcher_stopped")

    async def run_once(self) -> int:
        project_ids = await self._list_project_ids()
        dispatched = 0
        for project_id in project_ids:
            async with self._session_factory() as session:
                service = build_job_service(
                    session=session,
                    project_id=project_id,
                    settings=self._settings,
                    queue=self._queue,
                )
                recovery = await service.recover_expired(
                    limit=self._settings.jobs.dispatcher_batch_size
                )
                if recovery.failed:
                    documents = DocumentRepository(session, project_id)
                    builds = IndexBuildRepository(session, project_id)
                    for run in recovery.failed:
                        if run.document_id is not None:
                            document = await documents.get_by_id(
                                run.document_id, include_deleted=True
                            )
                            expected_version = run.payload.get("document_version")
                            if (
                                document is not None
                                and isinstance(expected_version, int)
                                and document.version == expected_version
                            ):
                                document.status = DocumentStatus.FAILED
                                document.error_message = run.failure_message

                        raw_build_id = run.payload.get("build_id")
                        if raw_build_id is None:
                            continue
                        try:
                            build_id = uuid.UUID(str(raw_build_id))
                        except (TypeError, ValueError):
                            continue
                        build = await builds.get_by_id(build_id, for_update=True)
                        if build is not None and build.state is IndexBuildState.BUILDING:
                            build.state = IndexBuildState.FAILED
                            build.failure_code = run.failure_code
                            build.failure_message = run.failure_message
                await session.commit()
                for _ in range(self._settings.jobs.dispatcher_batch_size):
                    if not await service.dispatch_next():
                        break
                    dispatched += 1
        return dispatched

    async def _list_project_ids(self) -> list[uuid.UUID]:
        async with self._session_factory() as session:
            result = await session.execute(select(Project.id).order_by(Project.id))
            return list(result.scalars().all())

    def stop(self) -> None:
        self._stop.set()


async def stop_dispatcher_task(
    dispatcher: DurableJobDispatcher | None,
    task: asyncio.Task[None] | None,
) -> None:
    if dispatcher is not None:
        dispatcher.stop()
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
