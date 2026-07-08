"""Integration tests for Organization API key authentication."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from tests.conftest import CapturingJobQueue, _integration_db_allowed

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

ADMIN_KEY = "ape_live_admin_integration_test_key_32bytes_long"
PEPPER = "integration-test-pepper-32-chars-min"


@pytest_asyncio.fixture
async def auth_db_client(
    require_postgres: None,
    apply_migrations: None,
    settings,
    captured_jobs,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    """HTTP client with auth enabled for integration tests."""
    from app.core.config import get_settings
    from app.dependencies.common import get_db_session
    from app.dependencies.knowledge import get_job_queue_dep
    from app.main import create_app
    from app.platform.db.session import Database
    from app.platform.jobs.implementations.job_queue_factory import get_job_queue
    from app.platform.providers.implementations.embedding_factory import get_embedding_provider
    from app.platform.providers.implementations.llm_factory import get_llm_provider
    from app.platform.providers.implementations.storage_factory import get_storage_provider
    from app.platform.providers.implementations.vector_store_factory import get_vector_store_provider

    allowed, reason = _integration_db_allowed(settings)
    if not allowed:
        pytest.skip(reason)

    monkeypatch.setenv("APE_AUTH__ENABLED", "true")
    monkeypatch.setenv("APE_AUTH__ADMIN_API_KEY", ADMIN_KEY)
    monkeypatch.setenv("APE_AUTH__KEY_PEPPER", PEPPER)
    monkeypatch.setenv("APE_AUTH__VERIFY_CACHE_BACKEND", "memory")
    monkeypatch.setenv("APE_AUTH__RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    get_storage_provider.cache_clear()
    get_job_queue.cache_clear()
    get_embedding_provider.cache_clear()
    get_vector_store_provider.cache_clear()
    get_llm_provider.cache_clear()

    app = create_app()
    database = Database(get_settings())
    connection: AsyncConnection = await database.engine.connect()
    transaction = await connection.begin()

    async def override_get_db_session() -> AsyncIterator[AsyncSession]:
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_job_queue_dep] = lambda: CapturingJobQueue(captured_jobs)

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac

    app.dependency_overrides.clear()
    await transaction.rollback()
    await connection.close()
    await database.dispose()


def admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_KEY}"}


async def _create_org_with_key(
    db_client: AsyncClient,
    *,
    name: str | None = None,
) -> tuple[str, str, str]:
    org_response = await db_client.post(
        "/api/v1/organizations",
        json={"name": name or f"Auth Org {uuid.uuid4().hex[:8]}"},
        headers=admin_headers(),
    )
    assert org_response.status_code == 201
    org_id = org_response.json()["data"]["id"]

    key_response = await db_client.post(
        f"/api/v1/organizations/{org_id}/api-keys",
        json={"name": "Integration"},
        headers=admin_headers(),
    )
    assert key_response.status_code == 201
    secret = key_response.json()["data"]["secret"]
    return org_id, secret, f"Bearer {secret}"


async def _create_project(
    db_client: AsyncClient,
    auth_header: str,
    *,
    name: str | None = None,
) -> str:
    response = await db_client.post(
        "/api/v1/projects",
        json={"name": name or f"Project {uuid.uuid4().hex[:8]}"},
        headers={"Authorization": auth_header},
    )
    assert response.status_code == 201
    return response.json()["data"]["id"]


async def test_organizations_require_admin_key(auth_db_client: AsyncClient) -> None:
    response = await auth_db_client.get("/api/v1/organizations")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.headers.get("www-authenticate") == 'Bearer realm="APE"'


async def test_projects_require_org_key_when_auth_enabled(auth_db_client: AsyncClient) -> None:
    response = await auth_db_client.post("/api/v1/projects", json={"name": "No Auth"})
    assert response.status_code == 401


async def test_org_key_allows_project_create(auth_db_client: AsyncClient) -> None:
    _, _, auth_header = await _create_org_with_key(auth_db_client)
    response = await auth_db_client.post(
        "/api/v1/projects",
        json={"name": f"Secured {uuid.uuid4().hex[:8]}"},
        headers={"Authorization": auth_header},
    )
    assert response.status_code == 201


async def test_x_api_key_header_authenticates(auth_db_client: AsyncClient) -> None:
    _, secret, _ = await _create_org_with_key(auth_db_client)
    response = await auth_db_client.get(
        "/api/v1/projects",
        headers={"X-API-Key": secret},
    )
    assert response.status_code == 200


async def test_wrong_key_returns_401(auth_db_client: AsyncClient) -> None:
    response = await auth_db_client.get(
        "/api/v1/projects",
        headers={"Authorization": "Bearer ape_live_invalid_key_value"},
    )
    assert response.status_code == 401


async def test_cross_org_project_isolation(auth_db_client: AsyncClient) -> None:
    _, _, auth_a = await _create_org_with_key(auth_db_client)
    _, _, auth_b = await _create_org_with_key(auth_db_client)

    project_id = await _create_project(auth_db_client, auth_a)

    cross_get = await auth_db_client.get(
        f"/api/v1/projects/{project_id}",
        headers={"Authorization": auth_b},
    )
    assert cross_get.status_code == 404
    assert cross_get.json()["error"]["code"] == "project_not_found"


async def test_cross_org_document_list_isolation(auth_db_client: AsyncClient) -> None:
    _, _, auth_a = await _create_org_with_key(auth_db_client)
    _, _, auth_b = await _create_org_with_key(auth_db_client)
    project_id = await _create_project(auth_db_client, auth_a)

    cross_list = await auth_db_client.get(
        f"/api/v1/projects/{project_id}/documents",
        headers={"Authorization": auth_b},
    )
    assert cross_list.status_code == 404
    assert cross_list.json()["error"]["code"] == "project_not_found"


async def test_cross_org_search_isolation(auth_db_client: AsyncClient) -> None:
    _, _, auth_a = await _create_org_with_key(auth_db_client)
    _, _, auth_b = await _create_org_with_key(auth_db_client)
    project_id = await _create_project(auth_db_client, auth_a)

    response = await auth_db_client.post(
        f"/api/v1/projects/{project_id}/search",
        json={"query": "hello"},
        headers={"Authorization": auth_b},
    )
    assert response.status_code == 404


async def test_cross_org_conversation_isolation(auth_db_client: AsyncClient) -> None:
    _, _, auth_a = await _create_org_with_key(auth_db_client)
    _, _, auth_b = await _create_org_with_key(auth_db_client)
    project_id = await _create_project(auth_db_client, auth_a)

    response = await auth_db_client.get(
        f"/api/v1/projects/{project_id}/conversations",
        headers={"Authorization": auth_b},
    )
    assert response.status_code == 404


async def test_revoked_key_returns_401_after_cache_warm(auth_db_client: AsyncClient) -> None:
    org_id, secret, auth_header = await _create_org_with_key(auth_db_client)

    warm = await auth_db_client.get("/api/v1/projects", headers={"Authorization": auth_header})
    assert warm.status_code == 200

    list_response = await auth_db_client.get(
        f"/api/v1/organizations/{org_id}/api-keys",
        headers=admin_headers(),
    )
    key_id = list_response.json()["data"]["items"][0]["id"]

    revoke = await auth_db_client.delete(
        f"/api/v1/organizations/{org_id}/api-keys/{key_id}",
        headers=admin_headers(),
    )
    assert revoke.status_code == 200

    response = await auth_db_client.get(
        "/api/v1/projects",
        headers={"Authorization": auth_header},
    )
    assert response.status_code == 401


async def test_inactive_organization_returns_401(auth_db_client: AsyncClient) -> None:
    org_id, _, auth_header = await _create_org_with_key(auth_db_client)

    await auth_db_client.get("/api/v1/projects", headers={"Authorization": auth_header})

    deactivate = await auth_db_client.patch(
        f"/api/v1/organizations/{org_id}/status",
        headers=admin_headers(),
    )
    assert deactivate.status_code == 200
    assert deactivate.json()["data"]["is_active"] is False

    response = await auth_db_client.get(
        "/api/v1/projects",
        headers={"Authorization": auth_header},
    )
    assert response.status_code == 401


async def test_rotate_revoke_old_invalidates_previous_key(auth_db_client: AsyncClient) -> None:
    org_id, _, auth_header = await _create_org_with_key(auth_db_client)

    await auth_db_client.get("/api/v1/projects", headers={"Authorization": auth_header})

    list_response = await auth_db_client.get(
        f"/api/v1/organizations/{org_id}/api-keys",
        headers=admin_headers(),
    )
    key_id = list_response.json()["data"]["items"][0]["id"]

    rotate = await auth_db_client.post(
        f"/api/v1/organizations/{org_id}/api-keys/{key_id}/rotate?revoke_old=true",
        headers=admin_headers(),
    )
    assert rotate.status_code == 201
    new_secret = rotate.json()["data"]["secret"]

    old_response = await auth_db_client.get(
        "/api/v1/projects",
        headers={"Authorization": auth_header},
    )
    new_response = await auth_db_client.get(
        "/api/v1/projects",
        headers={"Authorization": f"Bearer {new_secret}"},
    )
    assert old_response.status_code == 401
    assert new_response.status_code == 200


async def test_rotate_new_key_works_old_key_still_valid(auth_db_client: AsyncClient) -> None:
    org_id, _, auth_header = await _create_org_with_key(auth_db_client)
    list_response = await auth_db_client.get(
        f"/api/v1/organizations/{org_id}/api-keys",
        headers=admin_headers(),
    )
    key_id = list_response.json()["data"]["items"][0]["id"]

    rotate = await auth_db_client.post(
        f"/api/v1/organizations/{org_id}/api-keys/{key_id}/rotate",
        headers=admin_headers(),
    )
    assert rotate.status_code == 201
    new_secret = rotate.json()["data"]["secret"]

    old_response = await auth_db_client.get(
        "/api/v1/projects", headers={"Authorization": auth_header}
    )
    new_response = await auth_db_client.get(
        "/api/v1/projects",
        headers={"Authorization": f"Bearer {new_secret}"},
    )
    assert old_response.status_code == 200
    assert new_response.status_code == 200


async def test_last_used_at_persisted_on_authenticated_request(auth_db_client: AsyncClient) -> None:
    org_id, _, auth_header = await _create_org_with_key(auth_db_client)

    before = await auth_db_client.get(
        f"/api/v1/organizations/{org_id}/api-keys",
        headers=admin_headers(),
    )
    assert before.json()["data"]["items"][0]["last_used_at"] is None

    await auth_db_client.get("/api/v1/projects", headers={"Authorization": auth_header})

    after = await auth_db_client.get(
        f"/api/v1/organizations/{org_id}/api-keys",
        headers=admin_headers(),
    )
    assert after.json()["data"]["items"][0]["last_used_at"] is not None


async def test_cache_hit_skips_database_lookup(
    auth_db_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.organizations.repositories.api_key_repository import ApiKeyRepository

    _, _, auth_header = await _create_org_with_key(auth_db_client)
    original = ApiKeyRepository.get_by_key_hash
    mock_get = AsyncMock(side_effect=original)
    monkeypatch.setattr(ApiKeyRepository, "get_by_key_hash", mock_get)

    first = await auth_db_client.get("/api/v1/projects", headers={"Authorization": auth_header})
    second = await auth_db_client.get("/api/v1/projects", headers={"Authorization": auth_header})
    assert first.status_code == 200
    assert second.status_code == 200
    assert mock_get.await_count == 1


async def test_rate_limit_returns_429_with_retry_after(
    require_postgres: None,
    apply_migrations: None,
    settings,
    captured_jobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings
    from app.dependencies.common import get_db_session
    from app.dependencies.knowledge import get_job_queue_dep
    from app.main import create_app
    from app.platform.db.session import Database
    from app.platform.jobs.implementations.job_queue_factory import get_job_queue
    from app.platform.providers.implementations.embedding_factory import get_embedding_provider
    from app.platform.providers.implementations.llm_factory import get_llm_provider
    from app.platform.providers.implementations.storage_factory import get_storage_provider
    from app.platform.providers.implementations.vector_store_factory import get_vector_store_provider

    allowed, reason = _integration_db_allowed(settings)
    if not allowed:
        pytest.skip(reason)

    monkeypatch.setenv("APE_AUTH__ENABLED", "true")
    monkeypatch.setenv("APE_AUTH__ADMIN_API_KEY", ADMIN_KEY)
    monkeypatch.setenv("APE_AUTH__KEY_PEPPER", PEPPER)
    monkeypatch.setenv("APE_AUTH__VERIFY_CACHE_BACKEND", "memory")
    monkeypatch.setenv("APE_AUTH__RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("APE_AUTH__RATE_LIMIT_REQUESTS", "1")
    monkeypatch.setenv("APE_AUTH__RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("APE_AUTH__RATE_LIMIT_FAIL_OPEN", "false")
    get_settings.cache_clear()
    get_storage_provider.cache_clear()
    get_job_queue.cache_clear()
    get_embedding_provider.cache_clear()
    get_vector_store_provider.cache_clear()
    get_llm_provider.cache_clear()

    app = create_app()
    database = Database(get_settings())
    connection = await database.engine.connect()
    transaction = await connection.begin()

    async def override_get_db_session() -> AsyncIterator[AsyncSession]:
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_job_queue_dep] = lambda: CapturingJobQueue(captured_jobs)

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            _, _, auth_header = await _create_org_with_key(client)
            first = await client.get("/api/v1/projects", headers={"Authorization": auth_header})
            second = await client.get("/api/v1/projects", headers={"Authorization": auth_header})

    await transaction.rollback()
    await connection.close()
    await database.dispose()

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"
    assert second.headers.get("retry-after") is not None


async def test_openapi_includes_security_schemes(auth_db_client: AsyncClient) -> None:
    response = await auth_db_client.get("/openapi.json")
    assert response.status_code == 200
    components = response.json()["components"]["securitySchemes"]
    assert "OrganizationBearer" in components
    assert "OrganizationApiKey" in components
    assert "AdminBearer" in components
