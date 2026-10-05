"""Relationship writes and activation use the same project-scoped graph rules."""

import uuid
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.modules.knowledge.source_metadata_read import _canonical_source_scope
from tests.integration.test_phase3_source_retrieval import (
    _COOKIES,
    _CSRF,
    _project_id,
    _revision,
    _upload,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_replaces_beats_old_active_content_and_survives_metadata_correction(
    db_client: AsyncClient, integration_connection: AsyncConnection
):
    project = await _project_id(db_client)
    old = await _upload(db_client, project, "edition-old.txt", "old rule")
    new = await _upload(db_client, project, "edition-new.txt", "new rule")
    previous = await _revision(db_client, project, old, {"effective_from": "2020-01-01"})
    await _revision(
        db_client,
        project,
        new,
        {
            "effective_from": "2026-01-01",
            "source_group_id": previous["source_group_id"],
            "relationships": [
                {"relationship_type": "replaces", "target_revision_id": previous["id"]}
            ],
        },
    )
    # The old document remains active, and its metadata gets a newer revision
    # number. Neither fact may override the explicit complete replacement.
    await _revision(
        db_client,
        project,
        old,
        {"effective_from": "2020-01-01", "title": "Old edition corrected title"},
    )
    solo = await _upload(db_client, project, "solo.txt", "single content")
    state = (await db_client.get(f"/api/v1/projects/{project}/sources")).json()["data"]
    solo_revision = next(item["revision"] for item in state["items"] if item["document_id"] == solo)
    await _revision(
        db_client,
        project,
        solo,
        {
            "effective_from": "2020-01-01",
            "relationships": [
                {"relationship_type": "replaces", "target_revision_id": solo_revision["id"]}
            ],
        },
    )
    state = (await db_client.get(f"/api/v1/projects/{project}/sources")).json()["data"]
    for reference, historical in [(date(2026, 9, 7), False), (date(2024, 1, 1), True)]:
        scope = _canonical_source_scope(
            project_id=uuid.UUID(project),
            generation=state["generation"],
            reference_date=reference,
            historical=historical,
        )
        rows = (await integration_connection.execute(select(scope))).mappings().all()
        by_doc = {str(row["source_document_id"]): row for row in rows}
        assert by_doc[old]["source_policy_applicable"] is historical
        assert by_doc[new]["source_policy_applicable"] is not historical
        assert by_doc[solo]["source_policy_applicable"] is True
        if not historical:
            assert by_doc[old]["source_policy_exclusion_reason"] == "source_replaced"
            outgoing = by_doc[new]["source_relationships"] or []
            incoming = by_doc[old]["source_relationships"] or []
            assert any(edge.get("direction") == "outgoing" for edge in outgoing)
            assert any(edge.get("direction") == "incoming" for edge in incoming)
            assert any(edge.get("relationship_type") == "replaces" for edge in incoming)


async def test_multi_target_amendment_edit_replacement_and_cycle_rejection(db_client: AsyncClient):
    project = await _project_id(db_client)
    a = await _upload(db_client, project, "act-bn.txt", "Bangla base")
    b = await _upload(db_client, project, "act-en.txt", "English base")
    finance = await _upload(db_client, project, "finance.txt", "Amendment")
    state = (await db_client.get(f"/api/v1/projects/{project}/sources")).json()["data"]
    revisions = {item["document_id"]: item["revision"] for item in state["items"]}
    edges = [
        {
            "relationship_type": "modifies",
            "target_revision_id": revisions[doc]["id"],
            "target_provisions": ["section 78"] if doc == a else [],
        }
        for doc in [a, b]
    ]
    initial = await _revision(
        db_client,
        project,
        finance,
        {
            "effective_from": "2026-07-01",
            "relationships": edges,
        },
    )
    assert len(initial["relationships"]) == 2
    # A later unscoped edge must warn even when the first edge is scoped.
    assert "modifies_scope_unresolved_requires_current_rule_evidence" in initial["warnings"]
    corrected = await _revision(
        db_client,
        project,
        finance,
        {
            "effective_from": "2026-07-01",
            "title": "Finance corrected metadata",
            "relationships": edges,
        },
    )
    assert corrected["source_group_id"] == initial["source_group_id"]
    assert len(corrected["relationships"]) == 2
    reread = (
        await db_client.get(f"/api/v1/projects/{project}/sources/revisions/{corrected['id']}")
    ).json()["data"]
    assert {edge["target_revision_id"] for edge in reread["relationships"]} == {
        revisions[a]["id"],
        revisions[b]["id"],
    }

    replacement = await _upload(db_client, project, "finance-corrected.txt", "Complete new edition")
    replaced = await _revision(
        db_client,
        project,
        replacement,
        {
            "source_group_id": corrected["source_group_id"],
            "effective_from": "2026-07-01",
            "relationships": [
                {"relationship_type": "replaces", "target_revision_id": corrected["id"]},
                *edges,
            ],
        },
    )
    assert len(replaced["relationships"]) == 3
    reverse = await db_client.post(
        f"/api/v1/projects/{project}/sources/documents/{a}/revisions",
        json={
            "activate": True,
            "relationships": [
                {"relationship_type": "modifies", "target_revision_id": replaced["id"]}
            ],
        },
        headers=_CSRF,
        cookies=_COOKIES,
    )
    assert reverse.status_code == 400, reverse.text
    assert reverse.json()["error"]["code"] == "source_relationship_cycle"
    # Invalid activation must not append a generation or change the base pointer.
    after = (await db_client.get(f"/api/v1/projects/{project}/sources")).json()["data"]
    assert (
        next(item for item in after["items"] if item["document_id"] == a)["revision"]["id"]
        == revisions[a]["id"]
    )

    # Removing A from the current edition must also remove the obsolete edition's
    # edge from the active graph; the old file remains available for history.
    await _revision(
        db_client,
        project,
        replacement,
        {
            "relationships": [
                {"relationship_type": "replaces", "target_revision_id": corrected["id"]},
                edges[1],
            ],
            "effective_from": "2026-07-01",
        },
    )
    await _revision(
        db_client,
        project,
        finance,
        {
            "title": "Metadata correction on obsolete file",
            "relationships": edges,
            "effective_from": "2026-07-01",
        },
    )
    await _revision(
        db_client,
        project,
        a,
        {
            "relationships": [
                {"relationship_type": "modifies", "target_revision_id": replaced["id"]}
            ],
            "effective_from": "2026-07-01",
        },
    )
    rollback = await db_client.post(
        f"/api/v1/projects/{project}/sources/revisions/{replaced['id']}/activate",
        json={"reason": "Restore earlier amendment"},
        headers=_CSRF,
        cookies=_COOKIES,
    )
    assert rollback.status_code == 400, rollback.text
    assert rollback.json()["error"]["code"] == "source_relationship_cycle"


async def test_relationship_targets_reject_self_history_cross_project_and_duplicate_history(
    db_client: AsyncClient,
):
    project = await _project_id(db_client)
    a = await _upload(db_client, project, "base.txt", "base")
    b = await _upload(db_client, project, "amend.txt", "amendment")
    state = (await db_client.get(f"/api/v1/projects/{project}/sources")).json()["data"]
    original = next(item["revision"] for item in state["items"] if item["document_id"] == a)
    corrected = await _revision(db_client, project, a, {"title": "Base corrected"})
    url = f"/api/v1/projects/{project}/sources/documents"
    for document, payload, code in [
        (
            a,
            {
                "create_new_group": True,
                "relationships": [
                    {"relationship_type": "modifies", "target_revision_id": original["id"]}
                ],
            },
            "source_relationship_self_reference",
        ),
        (
            b,
            {
                "relationships": [
                    {"relationship_type": "modifies", "target_revision_id": revision["id"]}
                    for revision in [original, corrected]
                ]
            },
            "source_relationship_duplicate_history",
        ),
    ]:
        response = await db_client.post(
            f"{url}/{document}/revisions",
            json={**payload, "activate": True},
            headers=_CSRF,
            cookies=_COOKIES,
        )
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == code
    other_project = await _project_id(db_client)
    other = await _upload(db_client, other_project, "other.txt", "other")
    response = await db_client.post(
        f"/api/v1/projects/{other_project}/sources/documents/{other}/revisions",
        json={
            "relationships": [
                {"relationship_type": "modifies", "target_revision_id": original["id"]}
            ]
        },
        headers=_CSRF,
        cookies=_COOKIES,
    )
    assert response.status_code == 404, response.text


async def test_deleted_targets_are_hidden_from_current_choices_but_remain_in_history(
    db_client: AsyncClient, integration_connection: AsyncConnection
):
    project = await _project_id(db_client)
    base = await _upload(db_client, project, "base.txt", "base")
    amendment = await _upload(db_client, project, "amendment.txt", "amendment")
    url = f"/api/v1/projects/{project}/sources"
    before = (await db_client.get(url)).json()["data"]
    target = next(item["revision"] for item in before["items"] if item["document_id"] == base)
    await integration_connection.execute(
        text("update documents set deleted_at=now() where id=:id"), {"id": base}
    )
    current = (await db_client.get(url)).json()["data"]
    assert base not in {item["document_id"] for item in current["items"]}
    historical = (await db_client.get(url, params={"generation": before["generation"]})).json()[
        "data"
    ]
    assert base in {item["document_id"] for item in historical["items"]}
    invalid = await db_client.post(
        f"{url}/documents/{amendment}/revisions",
        json={
            "relationships": [{"relationship_type": "modifies", "target_revision_id": target["id"]}]
        },
        headers=_CSRF,
        cookies=_COOKIES,
    )
    assert invalid.status_code == 400, invalid.text
    assert invalid.json()["error"]["code"] == "source_relationship_target_deleted"


async def test_historical_selection_uses_corrected_metadata_without_reviving_null_dates(
    db_client: AsyncClient, integration_connection: AsyncConnection
):
    project = await _project_id(db_client)
    document = await _upload(
        db_client, project, "correction.txt", "Rule for a declared legal edition"
    )
    earlier = await _revision(
        db_client, project, document, {"effective_from": None, "title": "Before correction"}
    )
    before = (await db_client.get(f"/api/v1/projects/{project}/sources")).json()["data"]
    corrected = await _revision(
        db_client,
        project,
        document,
        {"effective_from": "2026-01-01", "title": "Corrected interval"},
    )
    after = (await db_client.get(f"/api/v1/projects/{project}/sources")).json()["data"]
    for generation, expected, applicable in [
        (before["generation"], earlier["id"], True),
        (after["generation"], corrected["id"], False),
    ]:
        scope = _canonical_source_scope(
            project_id=uuid.UUID(project),
            generation=generation,
            reference_date=date(2025, 1, 1),
            historical=True,
        )
        rows = (await integration_connection.execute(select(scope))).mappings().all()
        row = next(item for item in rows if str(item["source_document_id"]) == document)
        assert str(row["source_revision_id"]) == expected
        assert row["source_policy_applicable"] is applicable


async def test_explicit_editions_preserve_corrections_legacy_and_project_binding(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
):
    project = await _project_id(db_client)
    other_project = await _project_id(db_client)
    document = await _upload(db_client, project, "edition-history.txt", "Declared edition history")
    other_document = await _upload(db_client, other_project, "other.txt", "Other project")
    earlier = await _revision(
        db_client,
        project,
        document,
        {"edition_key": "edition.old", "work_key": "shared-work", "effective_from": None},
    )
    before = (await db_client.get(f"/api/v1/projects/{project}/sources")).json()["data"][
        "generation"
    ]
    corrected = await _revision(
        db_client,
        project,
        document,
        {
            "effective_from": "2020-01-01",
            "effective_to": "2025-12-31",
            "title": "Corrected old edition",
        },
    )
    assert corrected["edition_key"] == "edition.old"
    assert corrected["work_key"] == "shared-work"
    current = await _revision(
        db_client,
        project,
        document,
        {
            "edition_key": "edition.new",
            "effective_from": "2026-01-01",
            "relationships": [
                {"relationship_type": "replaces", "target_revision_id": corrected["id"]}
            ],
        },
    )
    assert current["source_group_id"] == corrected["source_group_id"]
    generation = (await db_client.get(f"/api/v1/projects/{project}/sources")).json()["data"][
        "generation"
    ]

    async def row_at(year, captured=generation):
        scope = _canonical_source_scope(
            project_id=uuid.UUID(project),
            generation=captured,
            reference_date=date(year, 1, 1),
            historical=True,
        )
        rows = (await integration_connection.execute(select(scope))).mappings().all()
        assert {str(row["source_document_id"]) for row in rows} == {document}
        assert other_document not in {str(row["source_document_id"]) for row in rows}
        return rows[0]

    row = await row_at(2024)
    assert str(row["source_revision_id"]) == corrected["id"]
    assert row["source_policy_applicable"] is True
    row = await row_at(2010)
    assert str(row["source_revision_id"]) != earlier["id"]
    assert row["source_policy_applicable"] is False
    row = await row_at(2010, before)
    assert str(row["source_revision_id"]) == earlier["id"]
    assert row["source_policy_applicable"] is True
    legacy = await _revision(
        db_client,
        project,
        document,
        {
            "edition_key": None,
            "effective_from": "2027-01-01",
            "title": "Unknown edition correction",
        },
    )
    generation = (await db_client.get(f"/api/v1/projects/{project}/sources")).json()["data"][
        "generation"
    ]
    row = await row_at(2024, generation)
    assert str(row["source_revision_id"]) == legacy["id"]
    assert row["source_policy_applicable"] is False
