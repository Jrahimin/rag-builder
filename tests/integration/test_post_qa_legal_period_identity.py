"""Exercise real pinned source eligibility and replacement SQL in PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.models.document_chunk import DocumentChunk
from app.modules.knowledge.source_metadata_read import _canonical_source_scope
from app.platform.jobs.contracts import JobDefinition
from tests.integration.test_phase3_source_retrieval import (
    _index_documents,
    _project_id,
    _revision,
    _upload,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_adjacent_ay_replacement_cannot_suppress_requested_historical_period(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
):
    project = await _project_id(db_client)
    old = await _upload(db_client, project, "historical-ay.txt", "AY 2025-26 historical threshold")
    newer = await _upload(db_client, project, "adjacent-ay.txt", "AY 2026-27 adjacent threshold")
    fiscal = await _upload(db_client, project, "same-numbers-fy.txt", "FY 2025-26 fiscal threshold")
    previous = await _revision(
        db_client,
        project,
        old,
        {
            "title": "Historical AY",
            "source_role": "primary",
            "effective_from": "2025-01-01",
            "published_date": "2025-01-01",
        },
    )
    await _revision(
        db_client,
        project,
        newer,
        {
            "title": "Adjacent AY",
            "source_role": "primary",
            "effective_from": "2026-01-01",
            "published_date": "2026-07-01",
            "source_group_id": previous["source_group_id"],
            "relationships": [
                {"relationship_type": "replaces", "target_revision_id": previous["id"]}
            ],
        },
    )
    await _revision(
        db_client,
        project,
        fiscal,
        {
            "title": "Fiscal distractor",
            "source_role": "primary",
            "effective_from": "2025-01-01",
        },
    )
    await _index_documents(
        db_client, integration_connection, captured_jobs, project, [old, newer, fiscal]
    )
    search = await db_client.post(
        f"/api/v1/projects/{project}/search",
        json={"query": "phase three governed policy comet identifier", "top_k": 20},
    )
    assert search.status_code == 200, search.text
    build = search.json()["data"]["diagnostics"]["index_build_id"]
    state = (await db_client.get(f"/api/v1/projects/{project}/sources")).json()["data"]

    async def applicable(periods, **extra):
        scope = _canonical_source_scope(
            project_id=uuid.UUID(project),
            generation=state["generation"],
            reference_date=date(2026, 9, 30),
            historical=False,
            request_scope={"index_build_id": build, "requested_periods": periods, **extra},
        )
        rows = (await integration_connection.execute(select(scope))).mappings().all()
        return {str(row["source_document_id"]): row for row in rows}

    historical = {"kind": "assessment", "start_year": 2025, "end_year": 2026}
    current = {"kind": "assessment", "start_year": 2026, "end_year": 2027}
    rows = await applicable([historical])
    assert rows[old]["source_policy_applicable"] is True
    assert rows[newer]["source_policy_applicable"] is False
    assert rows[fiscal]["source_policy_applicable"] is False
    assert rows[old]["source_policy_exclusion_reason"] != "source_replaced"

    rows = await applicable([current])
    assert rows[newer]["source_policy_applicable"] is True
    assert rows[old]["source_policy_applicable"] is False

    rows = await applicable([historical, current])
    assert rows[old]["source_policy_applicable"] is True
    assert rows[newer]["source_policy_applicable"] is True

    # A retrospective publication still cannot be used before it was available.
    rows = await applicable(
        [current],
        temporal_basis="known_at",
        exact_as_of="2026-01-01T00:00:00Z",
        known_at_inclusive=True,
    )
    assert rows[newer]["source_policy_applicable"] is False


async def test_replacement_periods_in_separate_chunks_apply_to_the_whole_document(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
):
    project = await _project_id(db_client)
    old = await _upload(db_client, project, "two-period-old.txt", "AY 2025-26 and AY 2026-27")
    newer = await _upload(
        db_client,
        project,
        "two-period-new.txt",
        "AY 2025-26 first schedule\n\n"
        + "Neutral source context. " * 350
        + "\n\nAY 2026-27 second schedule",
    )
    previous = await _revision(
        db_client,
        project,
        old,
        {
            "title": "Prior two-period edition",
            "source_role": "primary",
            "effective_from": "2025-01-01",
        },
    )
    await _revision(
        db_client,
        project,
        newer,
        {
            "title": "Replacing two-period edition",
            "source_role": "primary",
            "effective_from": "2025-01-01",
            "source_group_id": previous["source_group_id"],
            "relationships": [
                {"relationship_type": "replaces", "target_revision_id": previous["id"]}
            ],
        },
    )
    await _index_documents(db_client, integration_connection, captured_jobs, project, [old, newer])
    chunks = (
        (
            await integration_connection.execute(
                select(DocumentChunk.content).where(DocumentChunk.document_id == uuid.UUID(newer))
            )
        )
        .scalars()
        .all()
    )
    assert len(chunks) > 1
    assert not any("AY 2025-26" in content and "AY 2026-27" in content for content in chunks)
    search = await db_client.post(
        f"/api/v1/projects/{project}/search",
        json={
            "query": "phase three governed policy comet identifier",
            "top_k": 20,
        },
    )
    assert search.status_code == 200, search.text
    build = search.json()["data"]["diagnostics"]["index_build_id"]
    generation = (await db_client.get(f"/api/v1/projects/{project}/sources")).json()["data"][
        "generation"
    ]
    scope = _canonical_source_scope(
        project_id=uuid.UUID(project),
        generation=generation,
        reference_date=date(2026, 9, 30),
        historical=False,
        request_scope={
            "index_build_id": build,
            "requested_periods": [
                {"kind": "assessment", "start_year": 2025, "end_year": 2026},
                {"kind": "assessment", "start_year": 2026, "end_year": 2027},
            ],
        },
    )
    rows = {
        str(row["source_document_id"]): row
        for row in (await integration_connection.execute(select(scope))).mappings()
    }
    assert rows[newer]["source_policy_applicable"] is True
    assert rows[old]["source_policy_applicable"] is False
    assert rows[old]["source_policy_exclusion_reason"] == "source_replaced"
