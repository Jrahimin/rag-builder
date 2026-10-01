"""Real independent commits, project locks, expiry and atomic budget admission."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, update

from app.composition.jobs import build_job_service
from app.core.config import ProviderCostsConfig
from app.models.job_outbox import JobOutbox
from app.models.job_run import JobRun, JobState
from app.models.organization import Organization
from app.models.project import Project
from app.models.provider_work import EmbeddingCache, ProviderUsageAttempt
from app.platform.db.session import Database
from app.platform.infra.providers.provider_work_repository import (
    ProviderWorkRepository,
    purge_project_cache,
)
from app.platform.jobs.configuration import build_job_configuration
from app.platform.jobs.contracts import JobDefinition, JobQueue
from app.platform.providers.provider_work import ProviderBudgetError, ProviderWorkScope

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def committed_project(apply_migrations, settings):
    database = Database(settings)
    project_id, organization_id = uuid.uuid4(), uuid.uuid4()
    async with database.session_factory() as session, session.begin():
        session.add(Organization(id=organization_id, name="provider-controls-test"))
        await session.flush()
        session.add(Project(id=project_id, organization_id=organization_id, name=str(project_id)))
    try:
        yield database, project_id
    finally:
        async with database.session_factory() as session, session.begin():
            await session.execute(
                delete(ProviderUsageAttempt).where(ProviderUsageAttempt.project_id == project_id)
            )
            await session.execute(delete(Project).where(Project.id == project_id))
            await session.execute(delete(Organization).where(Organization.id == organization_id))
        await database.dispose()


def cost_scope(project, store, reference=None, **limits):
    return ProviderWorkScope(
        project,
        reference or str(uuid.uuid4()),
        "chat",
        "testing",
        3,
        ProviderCostsConfig(enabled=True, cache_enabled=True, enforce_budgets=True, **limits),
        store,
    )


async def test_cache_commit_survives_outer_rollback_and_expired_vectors_are_misses(
    committed_project,
):
    database, project = committed_project
    store = ProviderWorkRepository(database.session_factory)
    key = "a" * 64
    async with database.session_factory() as outer:
        await outer.execute(select(Project).where(Project.id == project))
        async with store.cache_session(project) as cache:
            await store.save_vectors(
                cache, project, {key: ({"purpose": "document"}, [1.0, 2.0])}, 30
            )
        await outer.rollback()
    async with database.session_factory() as session:
        assert await store.vectors(session, project, [key]) == {key: [1.0, 2.0]}
        await session.execute(
            update(EmbeddingCache)
            .where(EmbeddingCache.project_id == project)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
        assert await store.vectors(session, project, [key]) == {}
    async with store.cache_session(project) as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(EmbeddingCache)
                .where(EmbeddingCache.project_id == project)
            )
            == 0
        )


async def test_cache_lock_serializes_fills_and_purge_erases_project_cache(committed_project):
    database, project = committed_project
    store = ProviderWorkRepository(database.session_factory)
    key = "b" * 64
    fills = []

    async def fill():
        async with store.cache_session(project) as session:
            if not await store.vectors(session, project, [key]):
                fills.append(1)
                await asyncio.sleep(0.03)
                await store.save_vectors(session, project, {key: ({}, [1.0])}, 30)

    await asyncio.gather(fill(), fill(), fill())
    assert fills == [1]
    async with database.session_factory() as session, session.begin():
        await purge_project_cache(session, project)
    async with database.session_factory() as session:
        assert await store.vectors(session, project, [key]) == {}


async def test_operation_budget_counts_inflight_and_unknown_calls_atomically(committed_project):
    database, project = committed_project
    store = ProviderWorkRepository(database.session_factory)
    scope = cost_scope(project, store, operation_budget_usd=0.0001)
    outcomes = await asyncio.gather(
        *(store.reserve(scope, "/v2/embed", "embed-v4.0", "search_query", 60) for _ in range(2)),
        return_exceptions=True,
    )
    admitted = [outcome for outcome in outcomes if isinstance(outcome, uuid.UUID)]
    assert len(admitted) == 1
    assert sum(isinstance(outcome, ProviderBudgetError) for outcome in outcomes) == 1
    await store.complete(admitted[0], "unknown", None, None, None)
    with pytest.raises(ProviderBudgetError):
        await store.reserve(scope, "/v2/embed", "embed-v4.0", "search_query", 60)
    # A verified lower charge releases only the difference, not the whole attempt.
    await store.complete(admitted[0], "completed", 100, None, 12)
    await store.reserve(scope, "/v2/embed", "embed-v4.0", "search_query", 60)


async def test_monthly_budget_is_shared_across_operations_and_ledger_survives_project_purge(
    committed_project,
):
    database, project = committed_project
    store = ProviderWorkRepository(database.session_factory)
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    async with database.session_factory() as session:
        previous = await session.scalar(
            select(
                func.coalesce(
                    func.sum(
                        func.coalesce(
                            ProviderUsageAttempt.billed_micro_usd,
                            ProviderUsageAttempt.reserved_micro_usd,
                        )
                    ),
                    0,
                )
            ).where(ProviderUsageAttempt.created_at >= start)
        )
    scope = cost_scope(project, store, monthly_budget_usd=(int(previous or 0) + 100) / 1_000_000)
    await store.reserve(scope, "/v2/embed", "embed-v4.0", "search_query", 60)
    scope.reference = str(uuid.uuid4())
    with pytest.raises(ProviderBudgetError):
        await store.reserve(scope, "/v2/embed", "embed-v4.0", "search_query", 60)
    async with database.session_factory() as session, session.begin():
        await session.execute(delete(Project).where(Project.id == project))
    async with database.session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ProviderUsageAttempt)
                .where(ProviderUsageAttempt.project_id == project)
            )
            == 1
        )


async def test_ingestion_coalesces_with_fixed_deadline_and_queues_successor_when_running(
    committed_project, settings
):
    database, project = committed_project
    configuration = build_job_configuration(settings)
    queue = AsyncMock(spec=JobQueue)

    async def stage():
        async with database.session_factory() as session, session.begin():
            service = build_job_service(
                session=session, project_id=project, settings=settings, queue=queue
            )
            return await service.stage(
                JobDefinition(
                    name="document.embed",
                    project_id=project,
                    idempotency_key=str(uuid.uuid4()),
                    payload={
                        "coalesce_seconds": 10,
                        "embedding_set_version": settings.retrieval.embedding_set_version,
                    },
                ),
                configuration,
            )

    first, second = await asyncio.gather(stage(), stage())
    assert first.job_id == second.job_id
    assert {first.created, second.created} == {True, False}
    async with database.session_factory() as session, session.begin():
        outbox = await session.scalar(select(JobOutbox).where(JobOutbox.job_run_id == first.job_id))
        deadline = outbox.available_at
    assert deadline > datetime.now(UTC)
    third = await stage()
    assert third.job_id == first.job_id
    async with database.session_factory() as session, session.begin():
        assert (
            await session.scalar(
                select(JobOutbox.available_at).where(JobOutbox.job_run_id == first.job_id)
            )
            == deadline
        )
        await session.execute(
            update(JobRun).where(JobRun.id == first.job_id).values(state=JobState.RUNNING)
        )
    successor = await stage()
    assert successor.created and successor.job_id != first.job_id
