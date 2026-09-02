"""Database-backed Phase 1 policy, provenance, and ownership acceptance tests."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_CSRF = {"X-CSRF-Token": "phase1-test"}
_COOKIES = {"ape_admin_csrf": "phase1-test"}


async def _project(client: AsyncClient) -> dict[str, object]:
    response = await client.post(
        "/api/v1/projects",
        json={"name": f"Phase 1 {uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 201
    return response.json()["data"]


async def _revision(
    client: AsyncClient,
    project_id: str,
    *,
    policy_label: str,
    expected: str | None,
) -> dict[str, object]:
    response = await client.post(
        f"/api/v1/operator/projects/{project_id}/ai-config/revisions",
        json={
            "expected_active_revision_id": expected,
            "reason": f"Activate {policy_label}",
            "configuration": {
                "behavior": {"domain_instructions": f"Policy {policy_label}"},
                "execution": {},
            },
        },
        headers=_CSRF,
        cookies=_COOKIES,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def test_project_config_revision_history_and_optimistic_concurrency(
    db_client: AsyncClient,
) -> None:
    project = await _project(db_client)
    project_id = str(project["id"])
    assert project["ownership_locked"] is True

    first = await _revision(
        db_client,
        project_id,
        policy_label="phase1-v1",
        expected=None,
    )
    stale = await db_client.post(
        f"/api/v1/operator/projects/{project_id}/ai-config/revisions",
        json={
            "expected_active_revision_id": None,
            "reason": "Stale",
            "configuration": {
                "behavior": {"domain_instructions": "Policy stale"},
                "execution": {},
            },
        },
        headers=_CSRF,
        cookies=_COOKIES,
    )
    effective = await db_client.get(f"/api/v1/operator/projects/{project_id}/ai-config")
    history = await db_client.get(f"/api/v1/operator/projects/{project_id}/ai-config/revisions")

    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "project_config_revision_conflict"
    assert (
        effective.json()["data"]["configuration"]["domain_instructions"]
        == "Policy phase1-v1"
    )
    assert history.json()["data"][0]["id"] == first["id"]


async def test_existing_conversation_keeps_immutable_snapshot_after_policy_change(
    db_client: AsyncClient,
    integration_connection,
) -> None:
    project = await _project(db_client)
    project_id = str(project["id"])
    first = await _revision(
        db_client,
        project_id,
        policy_label="snapshot-v1",
        expected=None,
    )
    created = await db_client.post(
        f"/api/v1/projects/{project_id}/conversations",
        json={"title": "Frozen"},
    )
    assert created.status_code == 201
    conversation = created.json()["data"]

    await _revision(
        db_client,
        project_id,
        policy_label="snapshot-v2",
        expected=str(first["id"]),
    )
    turn = await db_client.post(
        f"/api/v1/projects/{project_id}/conversations/{conversation['id']}/messages",
        json={"content": "hello"},
    )
    assert turn.status_code == 200
    assistant = turn.json()["data"]["assistant_message"]
    assert assistant["config_snapshot_id"] == conversation["active_config_snapshot_id"]

    row = (
        await integration_connection.execute(
            text("SELECT config_provenance FROM messages WHERE id = :id"),
            {"id": uuid.UUID(assistant["id"])},
        )
    ).one()
    assert row.config_provenance["project_config_revision_id"] == str(first["id"])
    snapshot = (
        await integration_connection.execute(
            text(
                "SELECT configuration FROM conversation_config_snapshots "
                "WHERE id = :snapshot_id"
            ),
            {"snapshot_id": uuid.UUID(conversation["active_config_snapshot_id"])},
        )
    ).one()
    assert snapshot.configuration["domain_instructions"] == "Policy snapshot-v1"


async def test_new_project_ownership_is_locked_and_cannot_move(
    db_client: AsyncClient,
) -> None:
    project = await _project(db_client)
    response = await db_client.post(
        f"/api/v1/operator/projects/{project['id']}/ownership/reassign",
        json={
            "expected_current_organization_id": project["organization_id"],
            "target_organization_id": project["organization_id"],
            "reason": "Should remain locked",
        },
        headers=_CSRF,
        cookies=_COOKIES,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "project_ownership_locked"
