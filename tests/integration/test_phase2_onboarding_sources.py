"""Phase 2 onboarding, canonical Project admin, and source lifecycle acceptance."""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_CSRF = {"X-CSRF-Token": "phase2-test"}
_COOKIES = {"ape_admin_csrf": "phase2-test"}


async def _organization(client: AsyncClient, name: str | None = None) -> dict[str, object]:
    response = await client.post(
        "/api/v1/organizations",
        json={"name": name or f"Phase 2 Client {uuid.uuid4().hex[:8]}"},
        headers=_CSRF,
        cookies=_COOKIES,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def _operator_project(
    client: AsyncClient, organization_id: str, name: str | None = None
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/operator/projects",
        json={
            "name": name or f"Phase 2 Project {uuid.uuid4().hex[:8]}",
            "organization_id": organization_id,
        },
        headers=_CSRF,
        cookies=_COOKIES,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def test_operator_onboarding_lifecycle_and_replacement_first_rotation(
    db_client: AsyncClient,
) -> None:
    organization = await _organization(db_client)
    organization_id = str(organization["id"])
    project = await _operator_project(db_client, organization_id)

    created_key = await db_client.post(
        f"/api/v1/organizations/{organization_id}/api-keys",
        json={"name": "production"},
        headers=_CSRF,
        cookies=_COOKIES,
    )
    assert created_key.status_code == 201
    key = created_key.json()["data"]
    assert key["secret"].startswith("ape_")
    assert key["status"] == "active"
    assert key["created_by"] is not None

    rotated = await db_client.post(
        f"/api/v1/organizations/{organization_id}/api-keys/{key['id']}/rotate",
        json={"replacement_name": "production-next"},
        headers=_CSRF,
        cookies=_COOKIES,
    )
    assert rotated.status_code == 201
    replacement = rotated.json()["data"]
    assert replacement["name"] == "production-next"
    assert replacement["rotated_from_key_id"] == key["id"]

    keys = await db_client.get(
        f"/api/v1/organizations/{organization_id}/api-keys",
        headers=_CSRF,
        cookies=_COOKIES,
    )
    assert {item["status"] for item in keys.json()["data"]["items"]} == {"active"}

    associated = await db_client.get(
        f"/api/v1/organizations/{organization_id}/projects",
        headers=_CSRF,
        cookies=_COOKIES,
    )
    assert [item["id"] for item in associated.json()["data"]["items"]] == [project["id"]]

    disabled = await db_client.put(
        f"/api/v1/organizations/{organization_id}/status",
        json={"is_active": False},
        headers=_CSRF,
        cookies=_COOKIES,
    )
    assert disabled.json()["data"]["is_active"] is False
    blocked_key = await db_client.post(
        f"/api/v1/organizations/{organization_id}/api-keys",
        json={"name": "blocked"},
        headers=_CSRF,
        cookies=_COOKIES,
    )
    assert blocked_key.status_code == 409
    assert blocked_key.json()["error"]["code"] == "organization_disabled"

    archived = await db_client.post(
        f"/api/v1/organizations/{organization_id}/archive",
        headers=_CSRF,
        cookies=_COOKIES,
    )
    assert archived.json()["data"]["deleted_at"] is not None
    restored = await db_client.post(
        f"/api/v1/organizations/{organization_id}/restore",
        headers=_CSRF,
        cookies=_COOKIES,
    )
    assert restored.json()["data"]["deleted_at"] is None
    assert restored.json()["data"]["is_active"] is False


async def test_source_revisions_activation_history_and_generation_resolution(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
) -> None:
    organization = await _organization(db_client)
    project = await _operator_project(db_client, str(organization["id"]))
    project_id = str(project["id"])
    upload = await db_client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("policy.txt", b"governed source", "text/plain")},
    )
    assert upload.status_code == 201, upload.text
    document = upload.json()["data"]
    assert document["version"] == 1

    initial = await db_client.get(f"/api/v1/projects/{project_id}/sources")
    assert initial.status_code == 200
    initial_state = initial.json()["data"]
    assert initial_state["generation"] == 1
    assert initial_state["items"][0]["revision"]["lifecycle_status"] == "unspecified"
    first_revision = initial_state["items"][0]["revision"]

    revised = await db_client.post(
        f"/api/v1/projects/{project_id}/sources/documents/{document['id']}/revisions",
        json={
            "revision_label": "2026 edition",
            "title": "Governed policy",
            "effective_from": "2026-01-01",
            "effective_to": "2026-12-31",
            "lifecycle_status": "active",
            "source_role": "primary",
            "relationships": [
                {
                    "relationship_type": "replaces",
                    "target_revision_id": first_revision["id"],
                }
            ],
            "change_reason": "Publish the 2026 edition",
            "activate": True,
        },
        headers=_CSRF,
        cookies=_COOKIES,
    )
    assert revised.status_code == 201, revised.text
    revised_data = revised.json()["data"]
    assert revised_data["revision"]["revision_number"] == 2
    assert revised_data["activation"]["generation"] == 2

    historical = await db_client.get(f"/api/v1/projects/{project_id}/sources?generation=1")
    current = await db_client.get(f"/api/v1/projects/{project_id}/sources")
    assert historical.json()["data"]["items"][0]["revision"]["id"] == first_revision["id"]
    assert current.json()["data"]["items"][0]["revision"]["id"] == revised_data["revision"]["id"]

    persisted = (
        await integration_connection.execute(
            text("SELECT version FROM documents WHERE id = :document_id"),
            {"document_id": uuid.UUID(str(document["id"]))},
        )
    ).one()
    assert persisted.version == 1

    activations = await db_client.get(f"/api/v1/projects/{project_id}/sources/activations")
    assert [item["generation"] for item in activations.json()["data"]] == [2, 1]


async def test_optional_upload_source_metadata_and_cross_project_validation(
    db_client: AsyncClient,
) -> None:
    organization = await _organization(db_client)
    organization_id = str(organization["id"])
    project_a = await _operator_project(db_client, organization_id)
    project_b = await _operator_project(db_client, organization_id)

    upload_a = await db_client.post(
        f"/api/v1/projects/{project_a['id']}/documents",
        files={
            "file": ("base.txt", b"base source", "text/plain"),
            "source_metadata": (
                None,
                json.dumps(
                    {
                        "title": "Base source",
                        "revision_label": "v1",
                        "lifecycle_status": "active",
                        "source_role": "primary",
                        "effective_from": "2026-01-01",
                        "change_reason": "Initial governed upload",
                    }
                ),
            ),
        },
    )
    upload_b = await db_client.post(
        f"/api/v1/projects/{project_b['id']}/documents",
        files={"file": ("other.txt", b"other source", "text/plain")},
    )
    assert upload_a.status_code == 201, upload_a.text
    assert upload_b.status_code == 201, upload_b.text

    state_a = (await db_client.get(f"/api/v1/projects/{project_a['id']}/sources")).json()["data"]
    state_b = (await db_client.get(f"/api/v1/projects/{project_b['id']}/sources")).json()["data"]
    assert state_a["items"][0]["revision"]["title"] == "Base source"

    invalid = await db_client.post(
        f"/api/v1/projects/{project_a['id']}/sources/documents/{upload_a.json()['data']['id']}/revisions",
        json={
            "create_new_group": True,
            "title": "Invalid modifier",
            "relationships": [
                {
                    "relationship_type": "modifies",
                    "target_revision_id": state_b["items"][0]["revision"]["id"],
                }
            ],
            "change_reason": "Must not cross Projects",
        },
        headers=_CSRF,
        cookies=_COOKIES,
    )
    assert invalid.status_code == 404
    assert invalid.json()["error"]["code"] == "source_relationship_target_not_found"
