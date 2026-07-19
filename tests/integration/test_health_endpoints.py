"""Integration tests for the system health endpoints.

These exercise the full ASGI stack (middleware, DI, handlers) but do NOT
require external services to be running: readiness reports dependencies as
"down" rather than failing the request.
"""

from __future__ import annotations

from httpx import AsyncClient

from app.core.middleware import REQUEST_ID_HEADER, TRACE_ID_HEADER


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health/live")
    assert response.status_code == 200

    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "ok"
    assert body["data"]["environment"] == "testing"
    assert body["data"]["service"]
    assert body["data"]["version"]


async def test_health_sets_correlation_headers(client: AsyncClient) -> None:
    response = await client.get("/health/live")
    assert response.headers.get(REQUEST_ID_HEADER)
    assert response.headers.get(TRACE_ID_HEADER)


async def test_health_honors_inbound_request_id(client: AsyncClient) -> None:
    response = await client.get("/health/live", headers={REQUEST_ID_HEADER: "abc-123"})
    assert response.headers.get(REQUEST_ID_HEADER) == "abc-123"


async def test_ready_reports_dependency_breakdown(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    # 200 when every dependency is reachable, 503 otherwise - both are valid.
    assert response.status_code in (200, 503)

    body = response.json()
    assert body["success"] is True
    names = {dep["name"] for dep in body["data"]["dependencies"]}
    assert names == {
        "postgresql",
        "redis",
        "object_storage",
        "embedding_provider",
        "llm_provider",
        "reranker_provider",
        "ocr_provider",
    }
    provider_checks = [
        dependency
        for dependency in body["data"]["dependencies"]
        if dependency["name"].endswith("_provider")
    ]
    assert all(dependency["cached"] is True for dependency in provider_checks)


async def test_unknown_route_returns_standard_error(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404

    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["request_id"]
    assert body["error"]["details"] == {}


async def test_validation_error_uses_standard_field_details(client: AsyncClient) -> None:
    response = await client.get("/api/v1/projects/not-a-uuid")

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["request_id"]
    assert body["error"]["details"]["fields"][0]["field"] == "path.project_id"
