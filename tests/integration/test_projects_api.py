"""Integration tests for Project management API."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_create_project(db_client: AsyncClient) -> None:
    response = await db_client.post(
        "/api/v1/projects",
        json={"name": "Integration Alpha", "description": "test"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["name"] == "Integration Alpha"
    assert body["data"]["is_active"] is True
    assert body["data"]["deleted_at"] is None


async def test_create_duplicate_name(db_client: AsyncClient) -> None:
    await db_client.post("/api/v1/projects", json={"name": "Dup Name"})
    response = await db_client.post("/api/v1/projects", json={"name": "Dup Name"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "project_name_conflict"


async def test_list_projects_pagination(db_client: AsyncClient) -> None:
    await db_client.post("/api/v1/projects", json={"name": "List A"})
    await db_client.post("/api/v1/projects", json={"name": "List B"})

    response = await db_client.get("/api/v1/projects", params={"limit": 1, "offset": 0})
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["limit"] == 1
    assert body["data"]["total"] >= 2
    assert len(body["data"]["items"]) == 1


async def test_list_excludes_deleted_by_default(db_client: AsyncClient) -> None:
    create = await db_client.post("/api/v1/projects", json={"name": "To Delete"})
    project_id = create.json()["data"]["id"]
    await db_client.delete(f"/api/v1/projects/{project_id}")

    response = await db_client.get("/api/v1/projects")
    ids = {item["id"] for item in response.json()["data"]["items"]}
    assert project_id not in ids


async def test_list_include_deleted(db_client: AsyncClient) -> None:
    create = await db_client.post("/api/v1/projects", json={"name": "Deleted Visible"})
    project_id = create.json()["data"]["id"]
    await db_client.delete(f"/api/v1/projects/{project_id}")

    response = await db_client.get("/api/v1/projects", params={"include_deleted": True})
    ids = {item["id"] for item in response.json()["data"]["items"]}
    assert project_id in ids


async def test_list_filter_is_active(db_client: AsyncClient) -> None:
    create = await db_client.post("/api/v1/projects", json={"name": "Inactive Filter"})
    project_id = create.json()["data"]["id"]
    await db_client.patch(f"/api/v1/projects/{project_id}/status", json={"is_active": False})

    response = await db_client.get("/api/v1/projects", params={"is_active": False})
    ids = {item["id"] for item in response.json()["data"]["items"]}
    assert project_id in ids


async def test_get_deleted_returns_not_found(db_client: AsyncClient) -> None:
    create = await db_client.post("/api/v1/projects", json={"name": "Gone"})
    project_id = create.json()["data"]["id"]
    await db_client.delete(f"/api/v1/projects/{project_id}")

    response = await db_client.get(f"/api/v1/projects/{project_id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "project_not_found"


async def test_update_empty_body_returns_400(db_client: AsyncClient) -> None:
    create = await db_client.post("/api/v1/projects", json={"name": "No Empty Patch"})
    project_id = create.json()["data"]["id"]

    response = await db_client.patch(f"/api/v1/projects/{project_id}", json={})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "empty_update"


async def test_update_rejects_null_name(db_client: AsyncClient) -> None:
    create = await db_client.post("/api/v1/projects", json={"name": "Keep Name"})
    project_id = create.json()["data"]["id"]

    response = await db_client.patch(f"/api/v1/projects/{project_id}", json={"name": None})
    assert response.status_code == 422


async def test_create_rejects_unknown_fields(db_client: AsyncClient) -> None:
    response = await db_client.post(
        "/api/v1/projects",
        json={"name": "Valid", "typo_field": "x"},
    )
    assert response.status_code == 422


async def test_get_project(db_client: AsyncClient) -> None:
    create = await db_client.post("/api/v1/projects", json={"name": "Get Me"})
    project_id = create.json()["data"]["id"]

    response = await db_client.get(f"/api/v1/projects/{project_id}")
    assert response.status_code == 200
    assert response.json()["data"]["id"] == project_id


async def test_get_not_found(db_client: AsyncClient) -> None:
    response = await db_client.get(f"/api/v1/projects/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "project_not_found"


async def test_update_project(db_client: AsyncClient) -> None:
    create = await db_client.post("/api/v1/projects", json={"name": "Before"})
    project_id = create.json()["data"]["id"]

    response = await db_client.patch(
        f"/api/v1/projects/{project_id}",
        json={"name": "After", "description": "updated"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["name"] == "After"
    assert response.json()["data"]["description"] == "updated"


async def test_update_deleted_project(db_client: AsyncClient) -> None:
    create = await db_client.post("/api/v1/projects", json={"name": "No Update"})
    project_id = create.json()["data"]["id"]
    await db_client.delete(f"/api/v1/projects/{project_id}")

    response = await db_client.patch(
        f"/api/v1/projects/{project_id}",
        json={"name": "Nope"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "project_deleted"


async def test_set_active_status(db_client: AsyncClient) -> None:
    create = await db_client.post("/api/v1/projects", json={"name": "Toggle"})
    project_id = create.json()["data"]["id"]

    response = await db_client.patch(
        f"/api/v1/projects/{project_id}/status",
        json={"is_active": False},
    )
    assert response.status_code == 200
    assert response.json()["data"]["is_active"] is False


async def test_set_active_on_deleted_project(db_client: AsyncClient) -> None:
    create = await db_client.post("/api/v1/projects", json={"name": "No Toggle"})
    project_id = create.json()["data"]["id"]
    await db_client.delete(f"/api/v1/projects/{project_id}")

    response = await db_client.patch(
        f"/api/v1/projects/{project_id}/status",
        json={"is_active": True},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "project_deleted"


async def test_soft_delete_project(db_client: AsyncClient) -> None:
    create = await db_client.post("/api/v1/projects", json={"name": "Soft Del"})
    project_id = create.json()["data"]["id"]

    response = await db_client.delete(f"/api/v1/projects/{project_id}")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["deleted_at"] is not None
    assert data["deleted_by"] is None
    assert data["is_active"] is False


async def test_soft_delete_idempotent(db_client: AsyncClient) -> None:
    create = await db_client.post("/api/v1/projects", json={"name": "Idempotent Del"})
    project_id = create.json()["data"]["id"]

    first = await db_client.delete(f"/api/v1/projects/{project_id}")
    second = await db_client.delete(f"/api/v1/projects/{project_id}")
    assert first.status_code == 200
    assert second.status_code == 200
