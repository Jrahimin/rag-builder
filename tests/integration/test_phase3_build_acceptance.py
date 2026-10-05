"""Disposable SQL evidence for receipts, authorization, immutability and activation rejection."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.exceptions import UnauthorizedError
from app.dependencies.admin_auth import current_admin
from app.models.index_acceptance import IndexAcceptance
from app.models.index_build import IndexBuild, ProjectIndexPointer
from app.modules.retrieval.build_acceptance import digest
from tests.integration.build_acceptance_helpers import fixture_artifact
from tests.integration.knowledge_helpers import run_captured_lifecycle_jobs
from tests.integration.test_index_lifecycle_api import _ready_document

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_actual_receipt_storage_blocks_unaccepted_and_stale_activation(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs,
):
    project_id, _ = await _ready_document(db_client, integration_connection, captured_jobs)
    path = f"/api/v1/projects/{project_id}/index-builds"
    before = (await db_client.get(path)).json()["data"]["active_build_id"]
    staged = await db_client.post(f"{path}/reindex")
    build_id = uuid.UUID(staged.json()["data"]["build_id"])
    await run_captured_lifecycle_jobs(integration_connection, captured_jobs)
    missing = await db_client.post(f"{path}/{build_id}/activate")
    assert (
        missing.status_code == 400 and missing.json()["error"]["code"] == "index_acceptance_missing"
    )
    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        build = await session.get(IndexBuild, build_id)
        artifact = await fixture_artifact(session, build)
    csrf = {"X-CSRF-Token": "fixture", "Cookie": "ape_admin_csrf=fixture"}
    for field, value in [
        ("project_id", str(uuid.uuid4())),
        ("build_id", str(uuid.uuid4())),
        ("code_fingerprint", "f" * 64),
        ("configuration_hash", "e" * 64),
        ("source_generation", artifact.source_generation + 1),
        ("semantic_structure_hash", "b" * 64),
        ("index_configuration_hash", "e" * 64),
        ("corpus_fingerprint", "a" * 64),
        ("build_manifest_hash", "b" * 64),
        ("embedding_identity", {"embedding_provider": "cohere"}),
    ]:
        payload = artifact.model_dump(mode="json")
        payload[field] = value
        rejected = await db_client.post(f"{path}/{build_id}/acceptance", json=payload, headers=csrf)
        assert rejected.status_code == 400, rejected.text
        assert rejected.json()["error"]["code"] == "index_acceptance_stale"
        assert (await db_client.get(path)).json()["data"]["active_build_id"] == before
    accepted = await db_client.post(
        f"{path}/{build_id}/acceptance", json=artifact.model_dump(mode="json"), headers=csrf
    )
    assert accepted.status_code == 200, accepted.text
    saved = accepted.json()["data"]
    assert saved["artifact_hash"] == digest(artifact.model_dump(mode="json"))
    receipts = await db_client.get(f"{path}/{build_id}/acceptance")
    assert len(receipts.json()["data"]) == 1
    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        row = await session.scalar(
            select(IndexAcceptance).where(IndexAcceptance.id == uuid.UUID(saved["id"]))
        )
        assert row.artifact == artifact.model_dump(mode="json")
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(
                    update(IndexAcceptance)
                    .where(IndexAcceptance.id == row.id)
                    .values(created_by="tampered")
                )
    activation = await db_client.post(f"{path}/{build_id}/activate")
    assert activation.status_code == 200, activation.text
    assert (await db_client.get(path)).json()["data"]["active_build_id"] == str(build_id)
    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        pointer = await session.get(ProjectIndexPointer, uuid.UUID(project_id))
        assert pointer.previous_build_id == uuid.UUID(before)


async def test_acceptance_operator_authentication_and_cross_project_storage(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs,
):
    project_id, _ = await _ready_document(db_client, integration_connection, captured_jobs)
    other_id, _ = await _ready_document(db_client, integration_connection, captured_jobs)
    path = f"/api/v1/projects/{project_id}/index-builds"
    build_id = uuid.UUID((await db_client.post(f"{path}/reindex")).json()["data"]["build_id"])
    await run_captured_lifecycle_jobs(integration_connection, captured_jobs)

    async def deny():
        raise UnauthorizedError("fixture authorization denial")

    app = db_client._transport.app
    app.dependency_overrides[current_admin] = deny
    try:
        denied = await db_client.get(f"{path}/{build_id}/acceptance")
        assert denied.status_code == 401
    finally:
        app.dependency_overrides.pop(current_admin, None)
    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                session.add(
                    IndexAcceptance(
                        id=uuid.uuid4(),
                        project_id=uuid.UUID(other_id),
                        build_id=build_id,
                        artifact_hash="a" * 64,
                        artifact={},
                        created_by="cross-project",
                    )
                )
                await session.flush()


@pytest.mark.parametrize("governed", [True, False])
async def test_sql_period_eligibility_uses_reviewed_document_scope_only(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs,
    governed: bool,
):
    from app.models.document_chunk import DocumentChunk
    from app.modules.knowledge.scope_facts import ScopeFact, span_hash
    from app.modules.knowledge.source_metadata_read import KnowledgeSourceMetadataReader
    from tests.integration.knowledge_helpers import (
        run_captured_document_jobs,
        run_captured_embed_jobs,
    )

    project = await db_client.post("/api/v1/projects", json={"name": "Scope proof fixture"})
    project_id = uuid.UUID(project.json()["data"]["id"])
    text = "This document applies exclusively for Assessment Year 2026-27."
    upload = await db_client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("scope.txt", text.encode(), "text/plain")},
    )
    document_id = uuid.UUID(upload.json()["data"]["id"])
    await run_captured_document_jobs(integration_connection, captured_jobs)
    await db_client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/embed")
    await run_captured_embed_jobs(integration_connection, captured_jobs)
    if governed:
        from tests.integration.test_phase3_source_retrieval import _revision

        await _revision(
            db_client,
            str(project_id),
            str(document_id),
            {
                "title": "Reviewed exclusive assessment-year source",
                "source_role": "primary",
                "lifecycle_status": "active",
                "change_reason": "Synthetic source explicitly declares exclusive document scope",
            },
        )
    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        pointer = await session.get(ProjectIndexPointer, project_id)
        chunk = await session.scalar(
            select(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )
        span = {
            "text": chunk.content,
            "char_start": 0,
            "char_end": len(chunk.content),
            "provenance": "exact_source_span",
        }
        fact = ScopeFact(
            kind="period",
            value="2026-27",
            legal_kind="assessment",
            start_year=2026,
            end_year=2027,
            scope="governing",
            locality="document",
            locality_id=str(document_id),
            effect="operative",
            exhaustive=True,
            source_span=span,
            status="reviewed",
            review_provenance={
                "reviewer": "fixture",
                "evidence_hash": span_hash(chunk.content),
                "reason": "explicit exclusive source sentence",
            },
        )
        reader = KnowledgeSourceMetadataReader(session)

        async def eligible(facts, start=2025, kind="assessment", outer="scope.v2"):
            chunk.chunk_metadata = {"scope_fact_version": outer, "scope_facts": facts}
            await session.flush()
            scope = await reader.capture(
                project_id=project_id,
                generation=None,
                as_of=None,
                enforce=True,
                request_scope={
                    "index_build_id": str(pointer.active_build_id),
                    "requested_periods": [
                        {"kind": kind, "start_year": start, "end_year": start + 1}
                    ],
                },
            )
            ids = (
                (await session.execute(select(scope.selectable.c.source_document_id)))
                .scalars()
                .all()
            )
            return document_id in ids

        assert await eligible([])
        assert await eligible(
            [{"kind": "period", "legal_kind": "assessment", "start_year": 2026, "end_year": 2027}]
        )
        assert await eligible(
            [
                fact.model_copy(update={"scope": "mention", "exhaustive": False}).model_dump(
                    mode="json"
                )
            ]
        )
        assert await eligible(
            [fact.model_copy(update={"locality": "table"}).model_dump(mode="json")]
        )
        assert await eligible(
            [fact.model_copy(update={"effect": "proposal"}).model_dump(mode="json")]
        )
        assert await eligible([fact.model_dump(mode="json")]) is (not governed)
        assert await eligible([fact.model_dump(mode="json")], start=2026)
        assert await eligible([fact.model_dump(mode="json")], kind="fiscal") is (not governed)
        assert await eligible([fact.model_dump(mode="json")], kind="calendar") is (not governed)

        # Malformed persisted proof stays unknown/eligible; semantic publication rejects it.
        from copy import deepcopy

        from app.modules.retrieval.structural_contract import verify_semantic_scope_snapshot
        from app.platform.jobs.errors import PermanentJobError

        # No entire-document cross-kind exclusion can be inferred from these states.
        for changes in (
            {"scope": "mention", "exhaustive": False},
            {"locality": "table"},
            {"locality": "provision"},
            {"exhaustive": False},
            {"effect": "proposal"},
            {"effect": "example"},
            {"effect": "unknown"},
            {"status": "source_attested"},
            {"legal_kind": None, "start_year": None, "end_year": None},
        ):
            incomplete = fact.model_dump(mode="json")
            incomplete.update(changes)
            assert await eligible([incomplete], kind="fiscal")
            assert await eligible([incomplete], kind="calendar")
        for field in ("review_provenance", "source_span", "legal_kind"):
            damaged = fact.model_dump(mode="json")
            del damaged[field]
            assert await eligible([damaged], kind="fiscal")
        damaged = fact.model_dump(mode="json")
        damaged["review_provenance"]["evidence_hash"] = "f" * 64
        assert await eligible([damaged], kind="fiscal")
        damaged = fact.model_dump(mode="json")
        damaged["source_span"]["char_end"] = -1
        assert await eligible([damaged], kind="fiscal")
        for outer in (None, "scope.v1"):
            assert await eligible([fact.model_dump(mode="json")], kind="fiscal", outer=outer)

        for outer in (None, "scope.v1"):
            assert await eligible([fact.model_dump(mode="json")], outer=outer)
            with pytest.raises(PermanentJobError, match="Semantic scope"):
                build = await session.get(IndexBuild, pointer.active_build_id)
                await verify_semantic_scope_snapshot(session, project_id, build)
        for key, value in [("reviewer", ""), ("evidence_hash", "f" * 64)]:
            malformed = deepcopy(fact.model_dump(mode="json"))
            malformed["review_provenance"][key] = value
            assert await eligible([malformed])
            with pytest.raises(PermanentJobError):
                await verify_semantic_scope_snapshot(session, project_id, build)
        malformed = deepcopy(fact.model_dump(mode="json"))
        malformed["version"] = "scope.v1"
        assert await eligible([malformed])
        with pytest.raises(PermanentJobError):
            await verify_semantic_scope_snapshot(session, project_id, build)
        malformed = deepcopy(fact.model_dump(mode="json"))
        malformed["source_span"]["char_end"] = -1
        assert await eligible([malformed])
        with pytest.raises(PermanentJobError):
            await verify_semantic_scope_snapshot(session, project_id, build)


async def test_actual_two_projects_store_distinct_sets_and_reject_substitutions(
    db_client, integration_connection, captured_jobs
):
    projects = [
        await _ready_document(db_client, integration_connection, captured_jobs) for _ in range(2)
    ]
    artifacts = []
    csrf = {"X-CSRF-Token": "fixture", "Cookie": "ape_admin_csrf=fixture"}
    for index, (project_id, _) in enumerate(projects):
        path = f"/api/v1/projects/{project_id}/index-builds"
        staged = await db_client.post(path + "/reindex")
        build_id = uuid.UUID(staged.json()["data"]["build_id"])
        await run_captured_lifecycle_jobs(integration_connection, captured_jobs)
        async with AsyncSession(
            bind=integration_connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            build = await session.get(IndexBuild, build_id)
            artifact = await fixture_artifact(
                session, build, revision=f"project-{index}.v1", case_id=f"documentary-{index}"
            )
        accepted = await db_client.post(
            f"{path}/{build_id}/acceptance", json=artifact.model_dump(mode="json"), headers=csrf
        )
        assert accepted.status_code == 200, accepted.text
        artifacts.append(artifact)
    second = artifacts[1]
    bad = second.model_dump(mode="json")
    bad["acceptance_set_hash"] = artifacts[0].acceptance_set_hash
    bad["acceptance_set_revision"] = artifacts[0].acceptance_set_revision
    path = f"/api/v1/projects/{second.project_id}/index-builds/{second.build_id}"
    rejected = await db_client.post(path + "/acceptance", json=bad, headers=csrf)
    assert (
        rejected.status_code == 400
        and rejected.json()["error"]["code"] == "index_acceptance_incomplete"
    )
    definition = artifacts[0].model_dump(mode="json")
    cross = await db_client.post(path + "/acceptance", json=definition, headers=csrf)
    assert cross.status_code == 400 and cross.json()["error"]["code"] == "index_acceptance_stale"
