"""Real independent commits, project locks, expiry and atomic budget admission."""

from __future__ import annotations

import asyncio
import json
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


async def test_sealed_vectors_seed_exact_document_cache_and_readonly_preview(
    committed_project, settings, monkeypatch
):
    from unittest.mock import MagicMock

    from app.cli import provider_costs_cli
    from app.models.chunk_embedding import ChunkEmbedding
    from app.models.document import Document, DocumentStatus
    from app.models.document_chunk import DocumentChunk
    from app.models.index_build import IndexBuild, IndexBuildOperation, IndexBuildState
    from app.platform.domain.content_hash import content_hash
    from app.platform.providers.contracts.embedding import BaseEmbeddingProvider, EmbeddingPurpose
    from app.platform.providers.provider_work import (
        CachedEmbeddingProvider,
        attached_provider_scope,
        cache_identity,
        cache_key,
    )

    database, project = committed_project
    store = ProviderWorkRepository(database.session_factory)
    raw = MagicMock(spec=BaseEmbeddingProvider)
    raw.provider_name, raw.model_name = "cohere", "embed-v4.0"
    raw.dimensions, raw.provider_version = settings.embedding.dimensions, "1"
    raw.cache_namespace = "https://api.cohere.com"
    raw.embed_texts = AsyncMock(
        side_effect=AssertionError("Sealed reuse and preview must not contact Cohere")
    )
    text = "exact unchanged business policy"
    vector = [1.0] + [0.0] * (raw.dimensions - 1)
    build_id, document_id, chunk_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with database.session_factory() as session, session.begin():
        session.add(
            Document(
                id=document_id,
                project_id=project,
                filename="policy.txt",
                size_bytes=len(text),
                storage_key="test-only",
                content_sha256=content_hash(text),
                status=DocumentStatus.READY,
            )
        )
        session.add(
            IndexBuild(
                id=build_id,
                project_id=project,
                operation=IndexBuildOperation.INGEST,
                state=IndexBuildState.VALIDATED,
                configuration_hash="f" * 64,
                embedding_set_version=3,
                manifest={"embedding_origin": cache_identity(raw, 3, EmbeddingPurpose.DOCUMENT)},
            )
        )
        await session.flush()
        session.add(
            DocumentChunk(
                id=chunk_id,
                project_id=project,
                document_id=document_id,
                chunk_index=0,
                content=text,
            )
        )
        await session.flush()
        session.add(
            ChunkEmbedding(
                project_id=project,
                document_id=document_id,
                chunk_id=chunk_id,
                index_build_id=build_id,
                embedding_set_version=3,
                document_version=1,
                provider="cohere",
                model="embed-v4.0",
                dimensions=raw.dimensions,
                provider_version="1",
                input_content_hash=content_hash(text),
                embedding=vector,
            )
        )
    identity = cache_identity(raw, 3, EmbeddingPurpose.DOCUMENT)
    key = cache_key(identity, text)
    async with database.session_factory() as session:
        seeded = await store.seed_vectors(session, project, identity, {key: content_hash(text)})
        assert seeded == {key: vector}
        for incompatible in [
            {"purpose": "query"},
            {"namespace": "other-endpoint"},
            {"embedding_set_version": 4},
            {"provider_version": "2"},
            {"model": "other"},
        ]:
            assert (
                await store.seed_vectors(
                    session, project, {**identity, **incompatible}, {key: content_hash(text)}
                )
                == {}
            )
        assert (
            await store.seed_vectors(session, uuid.uuid4(), identity, {key: content_hash(text)})
            == {}
        )
        for origin, destination, matches in [
            (None, "https://api.cohere.com", False),
            ({**identity, "namespace": "sha256:custom"}, "https://api.cohere.com", False),
            (identity, "sha256:custom", False),
            ({**identity, "namespace": "sha256:custom"}, "sha256:custom", True),
        ]:
            await session.execute(
                update(IndexBuild)
                .where(IndexBuild.id == build_id)
                .values(manifest={"embedding_origin": origin})
            )
            result = await store.seed_vectors(
                session, project, {**identity, "namespace": destination}, {key: content_hash(text)}
            )
            assert bool(result) is matches
        await session.rollback()
    scope = cost_scope(project, store)
    with attached_provider_scope(scope):
        result = await CachedEmbeddingProvider(raw).embed_texts([text, text])
    assert result.vectors == [vector, vector] and result.billed_input_tokens == 0
    raw.embed_texts.assert_not_awaited()
    attempt = await store.reserve(scope, "/v2/embed", "embed-v4.0", "search_document", 100)
    await store.complete(attempt, "completed", 23, None, 3)
    live = settings.model_copy(
        update={
            "provider_costs": scope.config,
            "retrieval": settings.retrieval.model_copy(update={"embedding_set_version": 3}),
        }
    )
    monkeypatch.setattr(provider_costs_cli, "get_settings", lambda: live)
    monkeypatch.setattr(provider_costs_cli, "create_embedding_provider", lambda _: raw)
    report = await provider_costs_cli.report(
        project, datetime.now(UTC).strftime("%Y-%m"), True, scope.reference
    )
    assert report["build_preview"]["uncached_inputs"] == 0
    assert report["build_preview"]["estimated_micro_usd"] == 0
    assert json.dumps(report)
    assert report["usage"][0]["billed_tokens"] == 23
    assert report["usage"][0]["accounted_micro_usd"] == 3
    assert report["usage"][0]["day_utc"] == datetime.now(UTC).strftime("%Y-%m-%d")
    raw.embed_texts.assert_not_awaited()
    # The default target can differ from retained/active builds. Selecting the
    # same build identity as runtime must give the same cache/sealed reuse result.
    newer = live.model_copy(
        update={"retrieval": live.retrieval.model_copy(update={"embedding_set_version": 9})}
    )
    monkeypatch.setattr(provider_costs_cli, "get_settings", lambda: newer)
    monkeypatch.setattr(
        provider_costs_cli, "create_embedding_provider_for_identity", lambda *a, **kw: raw
    )
    default_preview = await provider_costs_cli.report(
        project, datetime.now(UTC).strftime("%Y-%m"), True
    )
    selected_preview = await provider_costs_cli.report(
        project, datetime.now(UTC).strftime("%Y-%m"), True, build_id=build_id
    )
    assert default_preview["build_preview"]["embedding_set_version"] == 9
    assert default_preview["build_preview"]["uncached_inputs"] == 1
    assert selected_preview["build_preview"]["embedding_set_version"] == 3
    assert selected_preview["build_preview"]["uncached_inputs"] == 0
    raw.embed_texts.assert_not_awaited()


async def _wait_for_pg_blocker(database, waiting_pid, blocking_pid):
    from sqlalchemy import text

    async def wait():
        async with database.session_factory() as observer:
            while True:
                blockers = await observer.scalar(
                    text("SELECT pg_blocking_pids(:pid)"), {"pid": waiting_pid}
                )
                if blocking_pid in blockers:
                    return
                await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), 5)


@pytest.mark.parametrize("producer_first", [True, False])
async def test_ingestion_join_and_claim_fence_source_commit_and_corpus_capture(
    committed_project, settings, producer_first
):
    from unittest.mock import MagicMock

    from sqlalchemy import text

    from app.models.document import Document, DocumentStatus
    from app.modules.jobs.repositories.job_run_repository import JobRunRepository
    from app.modules.retrieval.workflows.index_build_workflow import IndexBuildWorkflow

    database, project = committed_project
    configuration = build_job_configuration(settings)
    queue = AsyncMock(spec=JobQueue)

    async def stage(session):
        return await build_job_service(
            session=session, project_id=project, settings=settings, queue=queue
        ).stage(
            JobDefinition(
                name="document.embed",
                project_id=project,
                payload={"coalesce_seconds": 10, "embedding_set_version": 3},
            ),
            configuration,
        )

    async def capture(session):
        workflow = IndexBuildWorkflow(
            session,
            project,
            MagicMock(),
            embedding_set_version=3,
            batch_size=32,
            filterable_metadata_keys=[],
            fts_regconfig="simple",
        )
        return {doc.id for doc in await workflow._eligible_documents(exclude_document_id=None)}

    async with database.session_factory() as initial, initial.begin():
        first = await stage(initial)
    doc_id = uuid.uuid4()
    pid_ready = asyncio.Event()
    waiting_pid = None
    async with database.session_factory() as producer, database.session_factory() as worker:
        producer_pid = await producer.scalar(text("SELECT pg_backend_pid()"))
        worker_pid = await worker.scalar(text("SELECT pg_backend_pid()"))
        producer.add(
            Document(
                id=doc_id,
                project_id=project,
                filename="late.txt",
                size_bytes=4,
                storage_key="test-only",
                content_sha256="f" * 64,
                status=DocumentStatus.CHUNKED,
            )
        )
        await producer.flush()

        async def claim():
            nonlocal waiting_pid
            waiting_pid = worker_pid
            pid_ready.set()
            run = await JobRunRepository(worker, project).acquire(
                first.job_id, worker_id="barrier-worker", lease_seconds=60
            )
            assert run is not None
            await worker.commit()
            return await capture(worker)

        async def join():
            nonlocal waiting_pid
            waiting_pid = producer_pid
            pid_ready.set()
            joined = await stage(producer)
            await producer.commit()
            return joined

        if producer_first:
            joined = await stage(producer)
            assert joined.job_id == first.job_id and not joined.created
            task = asyncio.create_task(claim())
            try:
                await pid_ready.wait()
                await _wait_for_pg_blocker(database, waiting_pid, producer_pid)
                async with database.session_factory() as observer:
                    assert doc_id not in await capture(observer)
                await producer.commit()
                assert doc_id in await asyncio.wait_for(task, 5)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        else:
            assert (
                await JobRunRepository(worker, project).acquire(
                    first.job_id, worker_id="barrier-worker", lease_seconds=60
                )
                is not None
            )
            # Corpus captured by the earlier worker cannot see this uncommitted source.
            assert doc_id not in await capture(worker)
            task = asyncio.create_task(join())
            try:
                await pid_ready.wait()
                await _wait_for_pg_blocker(database, waiting_pid, worker_pid)
                await worker.commit()
                successor = await asyncio.wait_for(task, 5)
                assert successor.created and successor.job_id != first.job_id
                assert (
                    await JobRunRepository(worker, project).acquire(
                        successor.job_id, worker_id="successor-worker", lease_seconds=60
                    )
                    is not None
                )
                await worker.commit()
                assert doc_id in await capture(worker)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)


async def test_isolated_accounting_progresses_under_business_and_cache_pool_pressure(
    committed_project, settings
):
    from unittest.mock import MagicMock

    import httpx
    from sqlalchemy import text

    from app.platform.db.advisory_lock import project_stage_lock_key
    from app.platform.providers.contracts.embedding import (
        BaseEmbeddingProvider,
        EmbeddingBatchResult,
    )
    from app.platform.providers.provider_work import (
        CachedEmbeddingProvider,
        attached_provider_scope,
        metered_cohere_post,
    )

    _, project = committed_project
    tiny_settings = settings.model_copy(
        update={
            "database": settings.database.model_copy(
                update={"pool_size": 1, "max_overflow": 0, "pool_timeout": 0.5}
            )
        }
    )
    database = Database(tiny_settings)
    store = ProviderWorkRepository(
        database.provider_cache_session_factory, database.provider_accounting_session_factory
    )
    scope = cost_scope(project, store)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    raw = MagicMock(spec=BaseEmbeddingProvider)
    raw.provider_name, raw.model_name = "cohere", "embed-v4.0"
    raw.dimensions, raw.provider_version, raw.cache_namespace = 2, "1", "https://api.cohere.com"

    async def send():
        calls.append(1)
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"meta": {"billed_units": {"input_tokens": 5}}})

    async def embed(texts, *, purpose):
        await metered_cohere_post(send, "/v2/embed", {"model": "embed-v4.0", "texts": texts})
        return EmbeddingBatchResult([[1.0, 2.0] for _ in texts], "cohere", "embed-v4.0", 2, "1", 5)

    raw.embed_texts = embed
    cached = CachedEmbeddingProvider(raw)
    tasks = []
    try:
        async with database.session_factory() as business:
            await business.execute(text("SELECT 1"))  # occupy every business slot
            with attached_provider_scope(scope):
                tasks.append(asyncio.create_task(cached.embed_texts(["same input"])))
                await asyncio.wait_for(entered.wait(), 5)
                tasks.append(asyncio.create_task(cached.embed_texts(["same input"])))

                # Wait until a competing fill occupies the second cache slot and
                # is blocked behind the lock holder, rather than relying on sleeps.
                async def waiter_ready():
                    async with database.provider_accounting_session_factory() as observer:
                        lock_key = project_stage_lock_key(project, "embedding-cache") & (
                            (1 << 64) - 1
                        )
                        for _ in range(500):
                            if await observer.scalar(
                                text(
                                    "SELECT EXISTS (SELECT 1 FROM pg_locks "
                                    "WHERE locktype='advisory' "
                                    "AND NOT granted AND classid=:high AND objid=:low)"
                                ),
                                {"high": lock_key >> 32, "low": lock_key & ((1 << 32) - 1)},
                            ):
                                return
                            await asyncio.sleep(0.01)
                        raise AssertionError("Competing cache fill did not reach the lock barrier")

                await asyncio.wait_for(waiter_ready(), 5)

                async def purge():
                    async with (
                        database.provider_cache_session_factory() as session,
                        session.begin(),
                    ):
                        await purge_project_cache(session, project)

                tasks.append(asyncio.create_task(purge()))
                release.set()
                outcomes = await asyncio.wait_for(asyncio.gather(*tasks), 5)
                assert outcomes[0].vectors == outcomes[1].vectors == [[1.0, 2.0]]
                assert calls == [1]
            async with database.provider_accounting_session_factory() as observer:
                attempt = await observer.scalar(
                    select(ProviderUsageAttempt).where(
                        ProviderUsageAttempt.project_id == project,
                        ProviderUsageAttempt.reference == scope.reference,
                    )
                )
                assert attempt.status == "completed" and attempt.billed_tokens == 5
            await business.rollback()
        async with database.provider_cache_session_factory() as observer:
            assert not await observer.scalar(
                select(func.count())
                .select_from(EmbeddingCache)
                .where(EmbeddingCache.project_id == project)
            )
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await database.dispose()


async def test_accounting_completion_failure_keeps_real_cache_and_uncertain_reservation(
    committed_project, monkeypatch
):
    from unittest.mock import MagicMock

    import httpx

    from app.platform.providers.contracts.embedding import (
        BaseEmbeddingProvider,
        EmbeddingBatchResult,
    )
    from app.platform.providers.provider_work import (
        CachedEmbeddingProvider,
        attached_provider_scope,
        metered_cohere_post,
    )

    database, project = committed_project
    store = ProviderWorkRepository(
        database.provider_cache_session_factory, database.provider_accounting_session_factory
    )
    scope = cost_scope(project, store)
    send = AsyncMock(
        return_value=httpx.Response(200, json={"meta": {"billed_units": {"input_tokens": 5}}})
    )
    monkeypatch.setattr(store, "complete", AsyncMock(side_effect=TimeoutError("completion down")))
    raw = MagicMock(spec=BaseEmbeddingProvider)
    raw.provider_name, raw.model_name = "cohere", "embed-v4.0"
    raw.dimensions, raw.provider_version, raw.cache_namespace = 2, "1", "https://api.cohere.com"

    async def embed(texts, *, purpose):
        await metered_cohere_post(send, "/v2/embed", {"model": "embed-v4.0", "texts": texts})
        return EmbeddingBatchResult([[1.0, 2.0]], "cohere", "embed-v4.0", 2, "1", 5)

    raw.embed_texts = embed
    with attached_provider_scope(scope):
        first = await CachedEmbeddingProvider(raw).embed_texts(["already paid"])
        second = await CachedEmbeddingProvider(raw).embed_texts(["already paid"])
    assert first.vectors == second.vectors
    send.assert_awaited_once()
    async with database.session_factory() as observer:
        attempt = await observer.scalar(
            select(ProviderUsageAttempt).where(
                ProviderUsageAttempt.project_id == project,
                ProviderUsageAttempt.reference == scope.reference,
            )
        )
        assert attempt.status == "reserved" and attempt.billed_micro_usd is None
        assert attempt.reserved_micro_usd > 0
    with pytest.raises(ProviderBudgetError):
        await store.reserve(
            cost_scope(project, store, scope.reference, operation_budget_usd=0.000001),
            "/v2/embed",
            "embed-v4.0",
            "document",
            1,
        )


async def test_cancelled_fill_releases_pools_and_preserves_unknown_charge(committed_project):
    import httpx

    from app.platform.providers.provider_work import attached_provider_scope, metered_cohere_post

    database, project = committed_project
    store = ProviderWorkRepository(
        database.provider_cache_session_factory, database.provider_accounting_session_factory
    )
    scope = cost_scope(project, store)
    entered = asyncio.Event()

    async def send():
        entered.set()
        await asyncio.Event().wait()
        return httpx.Response(200)

    async def fill():
        with attached_provider_scope(scope):
            async with store.cache_session(project):
                await metered_cohere_post(
                    send, "/v2/embed", {"model": "embed-v4.0", "texts": ["cancelled input"]}
                )

    task = asyncio.create_task(fill())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with store.cache_session(project) as observer:
            assert not await observer.scalar(
                select(func.count())
                .select_from(EmbeddingCache)
                .where(EmbeddingCache.project_id == project)
            )
        async with database.provider_accounting_session_factory() as observer:
            attempt = await observer.scalar(
                select(ProviderUsageAttempt).where(
                    ProviderUsageAttempt.project_id == project,
                    ProviderUsageAttempt.reference == scope.reference,
                )
            )
            assert attempt.status == "unknown" and attempt.billed_micro_usd is None
            assert attempt.reserved_micro_usd > 0
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_coalesce_admission_separates_payload_versions_with_same_snapshot(
    committed_project, settings
):
    database, project = committed_project
    configuration = build_job_configuration(settings)
    jobs = []
    for version in (2, 4, 2):
        async with database.session_factory() as session, session.begin():
            jobs.append(
                await build_job_service(
                    session=session,
                    project_id=project,
                    settings=settings,
                    queue=AsyncMock(spec=JobQueue),
                ).stage(
                    JobDefinition(
                        name="document.embed",
                        project_id=project,
                        payload={"coalesce_seconds": 10, "embedding_set_version": version},
                    ),
                    configuration,
                )
            )
    assert jobs[0].created and jobs[1].created
    assert jobs[0].job_id != jobs[1].job_id
    assert jobs[2].job_id == jobs[0].job_id and not jobs[2].created


async def test_selected_preview_rejects_foreign_build_job_and_snapshot(
    committed_project, settings, monkeypatch
):
    from app.cli import provider_costs_cli
    from app.models.index_build import IndexBuild, IndexBuildOperation, IndexBuildState
    from app.models.job_configuration_snapshot import JobConfigurationSnapshot

    database, project = committed_project
    foreign_project = uuid.uuid4()
    configuration = build_job_configuration(settings)
    async with database.session_factory() as session, session.begin():
        original = await session.get(Project, project)
        session.add(
            Project(
                id=foreign_project, organization_id=original.organization_id, name="foreign-preview"
            )
        )
    monkeypatch.setattr(provider_costs_cli, "get_settings", lambda: settings)
    try:
        async with database.session_factory() as session, session.begin():
            foreign_job = await build_job_service(
                session=session,
                project_id=foreign_project,
                settings=settings,
                queue=AsyncMock(spec=JobQueue),
            ).stage(JobDefinition(name="corpus.reembed", project_id=foreign_project), configuration)
            foreign_reference_job = await build_job_service(
                session=session,
                project_id=foreign_project,
                settings=settings,
                queue=AsyncMock(spec=JobQueue),
            ).stage(
                JobDefinition(
                    name="corpus.reembed",
                    project_id=foreign_project,
                    idempotency_key=f"preview-foreign-reference:{uuid.uuid4()}",
                ),
                configuration,
            )
            local_job = await build_job_service(
                session=session,
                project_id=project,
                settings=settings,
                queue=AsyncMock(spec=JobQueue),
            ).stage(JobDefinition(name="corpus.reembed", project_id=project), configuration)
            foreign_build, local_build = uuid.uuid4(), uuid.uuid4()
            session.add_all(
                [
                    IndexBuild(
                        id=foreign_build,
                        project_id=foreign_project,
                        job_id=foreign_job.job_id,
                        state=IndexBuildState.BUILDING,
                        operation=IndexBuildOperation.REEMBED,
                        embedding_set_version=3,
                        configuration_hash="f" * 64,
                    ),
                    # Deliberately inconsistent persisted references must fail closed.
                    IndexBuild(
                        id=local_build,
                        project_id=project,
                        job_id=foreign_reference_job.job_id,
                        state=IndexBuildState.BUILDING,
                        operation=IndexBuildOperation.REEMBED,
                        embedding_set_version=3,
                        configuration_hash="f" * 64,
                    ),
                ]
            )
        month = datetime.now(UTC).strftime("%Y-%m")
        with pytest.raises(ValueError, match="build does not exist in this project"):
            await provider_costs_cli.report(project, month, True, build_id=foreign_build)
        with pytest.raises(ValueError, match="job does not exist in this project"):
            await provider_costs_cli.report(project, month, True, build_id=local_build)
        async with database.session_factory() as session, session.begin():
            foreign_snapshot = await session.scalar(
                select(JobConfigurationSnapshot.id).where(
                    JobConfigurationSnapshot.project_id == foreign_project
                )
            )
            await session.execute(
                update(IndexBuild)
                .where(IndexBuild.id == local_build)
                .values(job_id=local_job.job_id)
            )
            await session.execute(
                update(JobRun)
                .where(JobRun.id == local_job.job_id)
                .values(configuration_snapshot_id=foreign_snapshot)
            )
        with pytest.raises(ValueError, match="configuration snapshot is missing"):
            await provider_costs_cli.report(project, month, True, build_id=local_build)
    finally:
        # The test creates inconsistent references to exercise fail-closed reads;
        # clear local references before removing their foreign targets.
        async with database.session_factory() as session, session.begin():
            await session.execute(
                update(JobRun)
                .where(JobRun.project_id == project)
                .values(
                    configuration_snapshot_id=await session.scalar(
                        select(JobConfigurationSnapshot.id).where(
                            JobConfigurationSnapshot.project_id == project
                        )
                    )
                )
            )
            await session.execute(delete(Project).where(Project.id == foreign_project))
