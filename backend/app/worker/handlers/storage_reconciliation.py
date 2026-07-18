"""Durable storage/database reconciliation report."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.document import Document
from app.models.job_run import JobRun, JobType
from app.modules.jobs.services.job_service import JobService
from app.modules.knowledge.domain.document_storage_keys import iter_document_storage_keys
from app.platform.jobs.contracts import JobDefinition
from app.platform.providers.implementations.storage_factory import create_storage_provider
from app.worker.broker import broker
from app.worker.job_runtime import JobProgressReporter, run_durable_job


async def _reconcile(
    session: AsyncSession,
    run: JobRun,
    settings: Settings,
    jobs: JobService,
    reporter: JobProgressReporter,
) -> JobDefinition | None:
    del jobs
    await reporter.report("listing_database_artifacts", 20)
    result = await session.execute(
        select(Document).where(Document.project_id == run.project_id).order_by(Document.id)
    )
    expected = {
        key for document in result.scalars().all() for key in iter_document_storage_keys(document)
    }
    await reporter.report("listing_storage_artifacts", 55)
    actual = set(await create_storage_provider(settings).list_keys(f"{run.project_id}/"))
    run.result = {
        "expected_count": len(expected),
        "actual_count": len(actual),
        "missing": sorted(expected - actual),
        "orphan": sorted(actual - expected),
        "consistent": expected == actual,
    }
    await reporter.report("reconciled", 100)
    return None


@broker.task(task_name=JobType.STORAGE_RECONCILE.value)
async def storage_reconcile_task(*, project_id: str, job_id: str) -> None:
    await run_durable_job(
        project_id=project_id,
        job_id=job_id,
        expected_type=JobType.STORAGE_RECONCILE,
        operation=_reconcile,
    )
