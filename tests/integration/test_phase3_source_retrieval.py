"""Phase 3 source-aware retrieval, provenance, and compatibility acceptance."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from app.platform.jobs.contracts import JobDefinition
from tests.integration.knowledge_helpers import (
    run_captured_document_jobs,
    run_captured_embed_jobs,
    run_captured_evaluation_jobs,
    run_captured_index_jobs,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_CSRF = {"X-CSRF-Token": "phase3-test"}
_COOKIES = {"ape_admin_csrf": "phase3-test"}
_QUERY = "phase three governed policy comet identifier"


async def _project(client: AsyncClient) -> str:
    response = await client.post(
        "/api/v1/projects",
        json={"name": f"Phase 3 Retrieval {uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["data"]["id"])


async def _upload(client: AsyncClient, project_id: str, filename: str, suffix: str) -> str:
    response = await client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": (filename, f"{_QUERY} {suffix}".encode(), "text/plain")},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["data"]["id"])


async def _revision(
    client: AsyncClient,
    project_id: str,
    document_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    response = await client.post(
        f"/api/v1/projects/{project_id}/sources/documents/{document_id}/revisions",
        json={**payload, "activate": True},
        headers=_CSRF,
        cookies=_COOKIES,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["revision"]


async def _index_documents(
    client: AsyncClient,
    connection: AsyncConnection,
    jobs: list[JobDefinition],
    project_id: str,
    document_ids: list[str],
) -> None:
    await run_captured_document_jobs(connection, jobs)
    for document_id in document_ids:
        response = await client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/embed")
        assert response.status_code == 202, response.text
    await run_captured_embed_jobs(connection, jobs)
    for document_id in document_ids:
        response = await client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/index")
        assert response.status_code == 202, response.text
    await run_captured_index_jobs(connection, jobs)


async def test_current_historical_replacement_modifier_hybrid_and_legacy_behavior(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = await _project(db_client)
    old_document = await _upload(db_client, project_id, "old-policy.txt", "old revision")
    new_document = await _upload(db_client, project_id, "new-policy.txt", "new revision")
    modifier_document = await _upload(db_client, project_id, "amendment.txt", "modifier")
    draft_document = await _upload(db_client, project_id, "draft.txt", "draft")
    legacy_document = await _upload(db_client, project_id, "legacy.txt", "legacy unspecified")
    concurrent_document = await _upload(
        db_client, project_id, "concurrent.txt", "concurrent primary missing effective dates"
    )
    timeline_document = await _upload(
        db_client, project_id, "timeline.txt", "same document revision timeline"
    )
    overlap_document = await _upload(
        db_client, project_id, "overlap.txt", "overlapping effective intervals"
    )

    old_revision = await _revision(
        db_client,
        project_id,
        old_document,
        {
            "title": "Retired policy",
            "revision_label": "2000 edition",
            "effective_from": "2000-01-01",
            "effective_to": "2020-12-31",
            "lifecycle_status": "retired",
            "source_role": "primary",
            "change_reason": "Retire the historical policy",
        },
    )
    new_revision = await _revision(
        db_client,
        project_id,
        new_document,
        {
            "source_group_id": old_revision["source_group_id"],
            "title": "Current policy",
            "revision_label": "2021 edition",
            "effective_from": "2021-01-01",
            "effective_to": "9999-12-31",
            "lifecycle_status": "active",
            "source_role": "primary",
            "relationships": [
                {
                    "relationship_type": "replaces",
                    "target_revision_id": old_revision["id"],
                }
            ],
            "change_reason": "Replace the retired policy",
        },
    )
    modifier_revision = await _revision(
        db_client,
        project_id,
        modifier_document,
        {
            "title": "Policy amendment",
            "revision_label": "Amendment A",
            "effective_from": "2021-01-01",
            "effective_to": "9999-12-31",
            "lifecycle_status": "active",
            "source_role": "supporting",
            "relationships": [
                {
                    "relationship_type": "modifies",
                    "target_revision_id": new_revision["id"],
                }
            ],
            "change_reason": "Keep the modifier independently retrievable",
        },
    )
    concurrent_revision = await _revision(
        db_client,
        project_id,
        concurrent_document,
        {
            "title": "Concurrent primary source",
            "revision_label": "Undated active edition",
            "lifecycle_status": "active",
            "source_role": "primary",
            "change_reason": "Exercise concurrent primaries and missing-date warnings",
        },
    )
    assert "missing_effective_dates" in concurrent_revision["warnings"]
    await _revision(
        db_client,
        project_id,
        draft_document,
        {
            "title": "Unpublished draft",
            "revision_label": "Draft",
            "lifecycle_status": "draft",
            "source_role": "reference",
            "change_reason": "Exercise enforced draft exclusion",
        },
    )
    timeline_retired = await _revision(
        db_client,
        project_id,
        timeline_document,
        {
            "title": "Timeline historical revision",
            "revision_label": "Historical",
            "effective_from": "2000-01-01",
            "effective_to": "2020-12-31",
            "lifecycle_status": "retired",
            "source_role": "primary",
            "change_reason": "Retain a same-document historical revision",
        },
    )
    timeline_current = await _revision(
        db_client,
        project_id,
        timeline_document,
        {
            "title": "Timeline current revision",
            "revision_label": "Current",
            "effective_from": "2021-01-01",
            "effective_to": "9999-12-31",
            "lifecycle_status": "active",
            "source_role": "primary",
            "relationships": [
                {
                    "relationship_type": "replaces",
                    "target_revision_id": timeline_retired["id"],
                }
            ],
            "change_reason": "Supersede metadata on the same indexed document",
        },
    )
    overlap_previous = await _revision(
        db_client,
        project_id,
        overlap_document,
        {
            "title": "Overlapping policy previous revision",
            "revision_label": "Previous overlap",
            "effective_from": "2000-01-01",
            "effective_to": "2030-12-31",
            "lifecycle_status": "active",
            "source_role": "primary",
            "change_reason": "Create the earlier side of an overlap",
        },
    )
    overlap_current = await _revision(
        db_client,
        project_id,
        overlap_document,
        {
            "source_group_id": overlap_previous["source_group_id"],
            "title": "Overlapping policy current revision",
            "revision_label": "Current overlap",
            "effective_from": "2020-01-01",
            "effective_to": "2040-12-31",
            "lifecycle_status": "active",
            "source_role": "primary",
            "change_reason": "Exercise explicit overlap diagnostics",
        },
    )
    assert "overlapping_effective_interval" in overlap_current["warnings"]

    configured = await db_client.post(
        f"/api/v1/operator/projects/{project_id}/ai-config/revisions",
        json={
            "expected_active_revision_id": None,
            "reason": "Enable Phase 3 source enforcement",
            "configuration": {
                "behavior": {},
                "execution": {"retrieval_top_k": 20},
            },
        },
        headers=_CSRF,
        cookies=_COOKIES,
    )
    assert configured.status_code == 201, configured.text

    all_documents = [
        old_document,
        new_document,
        modifier_document,
        draft_document,
        legacy_document,
        concurrent_document,
        timeline_document,
        overlap_document,
    ]
    await _index_documents(
        db_client,
        integration_connection,
        captured_jobs,
        project_id,
        all_documents,
    )

    response = await db_client.post(
        f"/api/v1/projects/{project_id}/search",
        json={"query": _QUERY, "top_k": 20},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    ids = {str(item["document_id"]) for item in data["results"]}
    assert new_document in ids
    assert modifier_document in ids
    assert legacy_document in ids
    assert concurrent_document in ids
    assert timeline_document in ids
    assert overlap_document in ids
    assert old_document not in ids
    assert draft_document not in ids
    assert data["diagnostics"]["strategy"] == "hybrid"
    assert data["diagnostics"]["source_policy_status"] == "enforced"
    assert data["diagnostics"]["source_metadata_generation"] >= 1
    assert data["diagnostics"]["index_build_id"] is not None
    assert data["diagnostics"]["configuration_hash"] is not None
    current_hit = next(item for item in data["results"] if item["document_id"] == new_document)
    assert current_hit["metadata"]["source_revision_id"] == new_revision["id"]
    assert current_hit["metadata"]["source_title"] == "Current policy"
    assert current_hit["metadata"]["source_relationships"][0]["relationship_type"] == "replaces"
    timeline_hit = next(
        item for item in data["results"] if item["document_id"] == timeline_document
    )
    assert timeline_hit["metadata"]["source_revision_id"] == timeline_current["id"]
    overlap_hit = next(
        item for item in data["results"] if item["document_id"] == overlap_document
    )
    assert overlap_hit["metadata"]["source_revision_id"] == overlap_current["id"]

    historical = await db_client.post(
        f"/api/v1/projects/{project_id}/search",
        json={
            "query": _QUERY,
            "top_k": 20,
            "as_of": "2010-06-15T00:00:00Z",
        },
    )
    assert historical.status_code == 200, historical.text
    historical_data = historical.json()["data"]
    historical_ids = {str(item["document_id"]) for item in historical_data["results"]}
    assert old_document in historical_ids
    assert new_document not in historical_ids
    assert modifier_document not in historical_ids
    assert draft_document not in historical_ids
    assert legacy_document in historical_ids
    assert timeline_document in historical_ids
    assert overlap_document in historical_ids
    assert historical_data["diagnostics"]["reference_date"] == "2010-06-15"
    assert historical_data["diagnostics"]["as_of"] == "2010-06-15T00:00:00Z"
    historical_hit = next(
        item for item in historical_data["results"] if item["document_id"] == old_document
    )
    assert historical_hit["metadata"]["source_revision_id"] == old_revision["id"]
    historical_timeline_hit = next(
        item for item in historical_data["results"] if item["document_id"] == timeline_document
    )
    assert historical_timeline_hit["metadata"]["source_revision_id"] == timeline_retired["id"]
    historical_overlap_hit = next(
        item for item in historical_data["results"] if item["document_id"] == overlap_document
    )
    assert historical_overlap_hit["metadata"]["source_revision_id"] == overlap_previous["id"]
    assert modifier_revision["source_group_id"] != new_revision["source_group_id"]

    dataset = await db_client.post(
        f"/api/v1/projects/{project_id}/evaluations/datasets",
        json={
            "name": "phase3-source-policy-matrix",
            "version": "1.0.0",
            "cases": [
                {
                    "key": "current-replacement",
                    "kind": "citation",
                    "query": _QUERY,
                    "relevant_document_ids": [new_document],
                },
                {
                    "key": "historical-retired",
                    "kind": "citation",
                    "query": _QUERY,
                    "relevant_document_ids": [old_document],
                    "as_of": "2010-06-15T00:00:00Z",
                },
                {
                    "key": "partial-modifier",
                    "kind": "citation",
                    "query": _QUERY,
                    "relevant_document_ids": [modifier_document],
                },
                {
                    "key": "concurrent-primary-missing-dates",
                    "kind": "citation",
                    "query": _QUERY,
                    "relevant_document_ids": [concurrent_document],
                },
                {
                    "key": "overlapping-effective-dates",
                    "kind": "citation",
                    "query": _QUERY,
                    "relevant_document_ids": [overlap_document],
                },
                {
                    "key": "legacy-neutral",
                    "kind": "citation",
                    "query": _QUERY,
                    "relevant_document_ids": [legacy_document],
                },
            ],
        },
    )
    assert dataset.status_code == 201, dataset.text
    queued = await db_client.post(
        f"/api/v1/projects/{project_id}/evaluations/runs",
        json={"dataset_id": dataset.json()["data"]["id"], "top_k": 20},
    )
    assert queued.status_code == 202, queued.text
    run_id = queued.json()["data"]["id"]
    await run_captured_evaluation_jobs(integration_connection, captured_jobs)
    completed = await db_client.get(f"/api/v1/projects/{project_id}/evaluations/runs/{run_id}")
    assert completed.status_code == 200, completed.text
    run = completed.json()["data"]
    assert run["job_state"] == "succeeded", run
    assert run["index_build_id"] == historical_data["diagnostics"]["index_build_id"]
    assert (
        run["source_metadata_generation"]
        == historical_data["diagnostics"]["source_metadata_generation"]
    )
    semantic_cases = {
        item["case_key"]: item for item in run["case_results"] if item["profile"] == "semantic"
    }
    assert old_document in semantic_cases["historical-retired"]["result_document_ids"]
    assert new_document in semantic_cases["current-replacement"]["result_document_ids"]
    assert modifier_document in semantic_cases["partial-modifier"]["result_document_ids"]
    assert (
        concurrent_document
        in semantic_cases["concurrent-primary-missing-dates"]["result_document_ids"]
    )
    assert overlap_document in semantic_cases["overlapping-effective-dates"]["result_document_ids"]
    current_metadata = next(
        item
        for item in semantic_cases["current-replacement"]["result_source_metadata"]
        if item.get("source_revision_id") == new_revision["id"]
    )
    historical_metadata = next(
        item
        for item in semantic_cases["historical-retired"]["result_source_metadata"]
        if item.get("source_revision_id") == old_revision["id"]
    )
    assert current_metadata["source_revision_id"] == new_revision["id"]
    assert historical_metadata["source_revision_id"] == old_revision["id"]
    overlap_metadata = next(
        item
        for item in semantic_cases["overlapping-effective-dates"]["result_source_metadata"]
        if item.get("source_revision_id") == overlap_current["id"]
    )
    assert overlap_metadata["source_revision_id"] == overlap_current["id"]


async def test_depth_one_modifier_expansion_is_current_scoped_and_incoming_only(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = await _project(db_client)
    base_document = await _upload(
        db_client,
        project_id,
        "base-authority.txt",
        "base authority only",
    )
    modifier_document = await _upload(
        db_client,
        project_id,
        "modifier-authority.txt",
        "amendment text searched through the incoming edge",
    )
    unrelated_document = await _upload(
        db_client,
        project_id,
        "unrelated-authority.txt",
        "unrelated reference document",
    )
    base_revision = await _revision(
        db_client,
        project_id,
        base_document,
        {
            "title": "Base authority",
            "revision_label": "Base 2025",
            "published_date": "2025-01-01",
            "effective_from": "2025-01-01",
            "lifecycle_status": "active",
            "source_role": "primary",
            "change_reason": "Create expansion target",
        },
    )
    modifier_revision = await _revision(
        db_client,
        project_id,
        modifier_document,
        {
            "title": "Current amendment",
            "revision_label": "Amendment 2026",
            "published_date": "2026-01-01",
            "effective_from": "2026-01-01",
            "lifecycle_status": "active",
            "source_role": "supporting",
            "relationships": [
                {
                    "relationship_type": "modifies",
                    "target_revision_id": base_revision["id"],
                    "target_provisions": ["Section 21 — Investment Rebate Rate"],
                }
            ],
            "change_reason": "Modify the base authority",
        },
    )
    assert modifier_revision["relationships"][0]["target_provisions"] == [
        "Section 21 — Investment Rebate Rate"
    ]
    await _revision(
        db_client,
        project_id,
        unrelated_document,
        {
            "title": "Unrelated authority",
            "revision_label": "Unrelated 2025",
            "published_date": "2025-01-01",
            "effective_from": "2025-01-01",
            "lifecycle_status": "active",
            "source_role": "reference",
            "change_reason": "Verify expansion stays target-triggered",
        },
    )
    configured = await db_client.post(
        f"/api/v1/operator/projects/{project_id}/ai-config/revisions",
        json={
            "expected_active_revision_id": None,
            "reason": "Enable bounded current-authority expansion",
            "configuration": {
                "behavior": {},
                "execution": {"retrieval_top_k": 5},
            },
        },
        headers=_CSRF,
        cookies=_COOKIES,
    )
    assert configured.status_code == 201, configured.text
    await _index_documents(
        db_client,
        integration_connection,
        captured_jobs,
        project_id,
        [base_document, modifier_document, unrelated_document],
    )

    scoped = await db_client.post(
        f"/api/v1/projects/{project_id}/search",
        json={
            "query": "base authority only",
            "top_k": 5,
            "document_id": base_document,
        },
    )
    assert scoped.status_code == 200, scoped.text
    scoped_data = scoped.json()["data"]
    scoped_ids = {str(item["document_id"]) for item in scoped_data["results"]}
    assert base_document in scoped_ids
    assert modifier_document not in scoped_ids
    assert scoped_data["diagnostics"]["modifies_expansion_status"] == "suppressed_document_scope"
    assert scoped_data["diagnostics"]["modifies_expansion_depth"] == 1
    assert scoped_data["diagnostics"]["modifies_expansion_records"][0]["outcome"] == "expanded"
    assert scoped_data["diagnostics"]["modifies_expansion_records"][0]["modifier_document_id"] == (
        modifier_document
    )
    assert (
        scoped_data["diagnostics"]["modifies_expansion_records"][0]["modifier_revision_id"]
        == (modifier_revision["id"])
    )

    unscoped = await db_client.post(
        f"/api/v1/projects/{project_id}/search",
        json={
            "query": "base authority only",
            "top_k": 5,
        },
    )
    assert unscoped.status_code == 200, unscoped.text
    assert unscoped.json()["data"]["diagnostics"]["modifies_expansion_status"] != (
        "suppressed_document_scope"
    )

    historical = await db_client.post(
        f"/api/v1/projects/{project_id}/search",
        json={
            "query": "base authority only",
            "top_k": 5,
            "document_id": base_document,
            "as_of": "2025-06-01T00:00:00Z",
        },
    )
    assert historical.status_code == 200, historical.text
    historical_data = historical.json()["data"]
    assert modifier_document not in {
        str(item["document_id"]) for item in historical_data["results"]
    }
    assert historical_data["diagnostics"]["modifies_expansion_records"][0]["outcome"] == (
        "outside_as_of"
    )

    outgoing_only = await db_client.post(
        f"/api/v1/projects/{project_id}/search",
        json={
            "query": "amendment text searched through the incoming edge",
            "top_k": 5,
            "document_id": modifier_document,
        },
    )
    assert outgoing_only.status_code == 200, outgoing_only.text
    outgoing_data = outgoing_only.json()["data"]
    assert base_document not in {str(item["document_id"]) for item in outgoing_data["results"]}
    assert outgoing_data["diagnostics"]["modifies_expansion_status"] == "suppressed_document_scope"
