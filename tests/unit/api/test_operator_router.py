"""Operator route failure-envelope and metrics rendering tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.core.config import AppConfig, AuthConfig, Environment, Settings, get_settings
from app.core.exceptions import ServiceUnavailableError
from app.dependencies.operations import get_operator_service
from app.dependencies.projects import get_operator_project_service
from app.main import create_app
from app.platform.http.pagination import PaginatedResult


async def test_operator_metrics_failure_uses_standard_sanitized_envelope() -> None:
    app = create_app(Settings(app=AppConfig(env=Environment.TESTING)))
    service = AsyncMock()
    service.metrics.side_effect = ServiceUnavailableError(
        message="Operational data is temporarily unavailable.",
        code="operator_data_unavailable",
    )
    app.dependency_overrides[get_operator_service] = lambda: service
    async with (
        LifespanManager(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client,
    ):
        response = await client.get("/api/v1/operator/metrics")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "operator_data_unavailable"
    assert "database" not in response.text.lower()


async def test_operator_routes_require_super_admin_session_when_auth_is_enabled() -> None:
    settings = Settings(
        app=AppConfig(env=Environment.TESTING),
        auth=AuthConfig(
            enabled=True,
            key_pepper="pepper-with-at-least-thirty-two-random-bytes",
            admin_jwt_secret="admin-jwt-secret-with-at-least-thirty-two-bytes",
        ),
    )
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    async with (
        LifespanManager(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client,
    ):
        response = await client.get("/api/v1/operator/metrics")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_operator_projects_accept_admin_bulk_page_size() -> None:
    app = create_app(Settings(app=AppConfig(env=Environment.TESTING)))
    service = AsyncMock()
    service.list.return_value = PaginatedResult(items=[], total=0, limit=500, offset=0)
    app.dependency_overrides[get_operator_project_service] = lambda: service
    async with (
        LifespanManager(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client,
    ):
        response = await client.get("/api/v1/operator/projects?limit=500&include_deleted=true")
    assert response.status_code == 200
    params = service.list.await_args.args[0]
    assert params.limit == 500
    assert params.include_deleted is True
