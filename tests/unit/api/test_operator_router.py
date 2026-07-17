"""Operator route failure-envelope and metrics rendering tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.core.config import AppConfig, AuthConfig, Environment, Settings, get_settings
from app.core.exceptions import ServiceUnavailableError
from app.dependencies.operations import get_operator_service
from app.main import create_app


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


async def test_operator_routes_require_admin_key_when_auth_is_enabled() -> None:
    settings = Settings(
        app=AppConfig(env=Environment.TESTING),
        auth=AuthConfig(
            enabled=True,
            admin_api_key="admin-key-with-at-least-thirty-two-bytes",
            key_pepper="pepper-with-at-least-thirty-two-random-bytes",
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
