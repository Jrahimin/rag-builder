"""Scope lifetime, paid QA admission and skipped startup probes."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cli import rag_journey_cli
from app.composition.provider_work import ProviderWorkMiddleware, provider_work_scope
from app.core.config import EmbeddingBackend, ProviderCostsConfig, Settings
from app.platform.providers.provider_work import current_provider_scope
from app.platform.system.preflight_service import StartupPreflightService
from app.platform.system.schemas import DependencyState

pytestmark = pytest.mark.unit


async def test_http_scope_stays_attached_through_sse_delivery_and_resets():
    project = uuid.uuid4()
    settings = Settings(provider_costs=ProviderCostsConfig(enabled=True))
    observed = []

    async def app(scope, receive, send):
        observed.append(current_provider_scope())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"event:done\n\n"})

    async def send(message):
        observed.append(current_provider_scope())

    instance = SimpleNamespace(
        state=SimpleNamespace(
            settings=settings,
            db=SimpleNamespace(
                session_factory=MagicMock(),
                provider_accounting_session_factory=MagicMock(),
                provider_cache_session_factory=MagicMock(),
            ),
        )
    )
    await ProviderWorkMiddleware(app)(
        {
            "type": "http",
            "path": f"/api/v1/projects/{project}/conversations/x/messages",
            "app": instance,
        },
        AsyncMock(),
        send,
    )
    assert len(observed) == 3
    assert all(scope.project_id == project and scope.workload == "chat" for scope in observed)
    assert current_provider_scope() is None


def test_inline_qa_child_jobs_share_parent_budget_and_reset_scope():
    project = uuid.uuid4()
    sessions = MagicMock()
    settings = Settings()
    paid_settings = settings.model_copy(
        update={
            "provider_costs": ProviderCostsConfig(
                enabled=True,
                enforce_budgets=True,
                paid_evaluation_enabled=True,
                evaluation_budget_usd=0.5,
            )
        }
    )
    with provider_work_scope(paid_settings, sessions, project, "whole-run", "evaluation") as parent:
        with provider_work_scope(
            settings, sessions, project, "child-job", "document.embed"
        ) as child:
            assert child.reference == "whole-run"
            assert child.workload == "evaluation"
            assert child.config is parent.config
            assert child.store is parent.store
        assert current_provider_scope() is parent
    assert current_provider_scope() is None


@pytest.mark.parametrize("amount", ["nan", "inf", "0", "-1"])
def test_cli_rejects_invalid_paid_budget_before_creating_project(amount, monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(rag_journey_cli, "run_journey", run)
    assert rag_journey_cli.main(["--paid-eval", "--budget-usd", amount]) == 2
    run.assert_not_awaited()


async def test_accounting_skips_paid_startup_probe_with_explicit_unverified_status():
    settings = Settings(provider_costs=ProviderCostsConfig(enabled=True))
    settings = settings.model_copy(
        update={
            "embedding": settings.embedding.model_copy(update={"backend": EmbeddingBackend.COHERE})
        }
    )
    probe = AsyncMock()
    service = StartupPreflightService(
        settings=settings, database=MagicMock(), redis=MagicMock(), storage=MagicMock()
    )
    result = await service._check("embedding_provider", probe, check_timeout=1, action="verify")
    assert result.state is DependencyState.SKIPPED
    assert "unverified" in result.detail
    probe.assert_not_awaited()


def test_cli_requires_paid_flag_even_when_deployment_allows_evaluation(monkeypatch):
    settings = Settings(
        provider_costs=ProviderCostsConfig(
            enabled=True, enforce_budgets=True, paid_evaluation_enabled=True
        )
    )
    settings = settings.model_copy(
        update={
            "embedding": settings.embedding.model_copy(update={"backend": EmbeddingBackend.COHERE})
        }
    )
    monkeypatch.setattr(rag_journey_cli, "get_settings", MagicMock(return_value=settings))
    run = AsyncMock()
    monkeypatch.setattr(rag_journey_cli, "run_journey", run)
    assert rag_journey_cli.main([]) == 2
    run.assert_not_awaited()


async def test_database_owns_lazy_provider_pools_and_closes_every_pool_on_shutdown(monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncEngine

    from app.platform.db import session as module

    engines = [MagicMock(spec=AsyncEngine) for _ in range(3)]
    for engine in engines:
        engine.dispose = AsyncMock()
    factory = MagicMock(side_effect=engines)
    monkeypatch.setattr(module, "create_async_engine", factory)
    database = module.Database(Settings())
    assert factory.call_count == 1
    cache = database.provider_cache_session_factory
    accounting = database.provider_accounting_session_factory
    assert cache is database.provider_cache_session_factory
    assert accounting is database.provider_accounting_session_factory
    assert cache.kw["bind"] is not accounting.kw["bind"]
    assert factory.call_count == 3
    for call in factory.call_args_list[1:]:
        assert call.kwargs["pool_size"] == 2 and call.kwargs["max_overflow"] == 0
    engines[1].dispose.side_effect = RuntimeError("cache dispose failed")
    with pytest.raises(RuntimeError, match="cache dispose failed"):
        await database.dispose()
    for engine in engines:
        engine.dispose.assert_awaited_once()
