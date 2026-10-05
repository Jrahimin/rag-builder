"""Disposable SQL/API proof for reviewed overlays, candidate evaluation and observed receipts."""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_chunk import DocumentChunk
from app.models.index_acceptance import IndexAcceptanceReport
from app.models.index_build import IndexBuild
from app.models.index_scope_review import IndexScopeReview
from app.models.project import Project
from app.models.source_metadata import SourceMetadataRevision
from app.modules.knowledge.source_metadata_read import _canonical_source_scope
from app.modules.retrieval.build_acceptance import digest, semantic_snapshot_hash
from app.platform.domain.content_hash import content_hash
from app.platform.domain.source_scope import ScopeFact, span_hash
from tests.integration.build_acceptance_helpers import attest_fixture_build
from tests.integration.knowledge_helpers import (
    run_captured_evaluation_jobs,
    run_captured_lifecycle_jobs,
)
from tests.integration.test_phase3_source_retrieval import (
    _index_documents,
    _project_id,
    _revision,
    _upload,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
CSRF = {"X-CSRF-Token": "completion", "Cookie": "ape_admin_csrf=completion"}


async def candidate(client, connection, jobs, project):
    path = f"/api/v1/projects/{project}/index-builds"
    staged = await client.post(path + "/reindex")
    assert staged.status_code == 202, staged.text
    await run_captured_lifecycle_jobs(connection, jobs)
    return staged.json()["data"]["build_id"]


async def test_reviewed_scope_overlay_is_immutable_project_bound_and_consumed_by_sql(
    db_client, integration_connection, captured_jobs
):
    project = await _project_id(db_client)
    document = await _upload(
        db_client,
        project,
        "operative-scope.md",
        "\n# Operative scope\n"
        "This document applies exclusively to Assessment Year 2025-26. "
        "Its threshold is BDT 350000.",
    )
    revision = await _revision(
        db_client,
        project,
        document,
        {"title": "Reviewed historical scope", "source_role": "primary"},
    )
    await _index_documents(db_client, integration_connection, captured_jobs, project, [document])
    build = await candidate(db_client, integration_connection, captured_jobs, project)
    path = f"/api/v1/projects/{project}/index-builds/{build}"
    before = (await db_client.get(path + "/acceptance-identity")).json()["data"]
    active = (await db_client.get(f"/api/v1/projects/{project}/index-builds")).json()["data"][
        "active_build_id"
    ]
    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        chunk = await session.scalar(
            select(DocumentChunk)
            .where(
                DocumentChunk.project_id == uuid.UUID(project),
                DocumentChunk.document_id == uuid.UUID(document),
            )
            .order_by(DocumentChunk.chunk_index)
        )
        source = await session.get(SourceMetadataRevision, uuid.UUID(str(revision["id"])))
        state = await session.get(Project, uuid.UUID(project))
        assert chunk is not None and source is not None and chunk.char_start is not None
        original_metadata = deepcopy(chunk.chunk_metadata)
        quote = "This document applies exclusively to Assessment Year 2025-26."
        offset = chunk.content.index(quote) + chunk.char_start
        fact = ScopeFact(
            kind="period",
            value="2025-26",
            legal_kind="assessment",
            start_year=2025,
            end_year=2026,
            scope="governing",
            locality="document",
            locality_id=str(document),
            effect="operative",
            exhaustive=True,
            status="reviewed",
            source_span={
                "text": quote,
                "char_start": offset,
                "char_end": offset + len(quote),
                "provenance": "exact_source_span",
            },
            review_provenance={
                "reviewer": "requested-reviewer",
                "evidence_hash": span_hash(quote),
                "reason": "Explicit exhaustive operative document statement",
            },
        )
        body = {
            "chunk_id": str(chunk.id),
            "source_revision_id": str(source.id),
            "source_generation": state.source_metadata_generation,
            "source_content_hash": source.content_hash,
            "chunk_hash": content_hash(chunk.content),
            "facts": [fact.model_dump(mode="json")],
        }
        chunk_id = chunk.id

        async def applicable():
            scope = _canonical_source_scope(
                project_id=uuid.UUID(project),
                generation=state.source_metadata_generation,
                reference_date=datetime.now(UTC).date(),
                historical=False,
                request_scope={
                    "index_build_id": build,
                    "requested_periods": [{"kind": "fiscal", "start_year": 2025, "end_year": 2026}],
                },
            )
            return await session.scalar(
                select(scope.c.source_policy_applicable).where(
                    scope.c.source_document_id == uuid.UUID(document)
                )
            )

        assert await applicable() is True
        wrong = deepcopy(body)
        wrong["facts"][0]["start_year"] = 2024
        invalid = await db_client.post(path + "/scope-reviews", json=wrong, headers=CSRF)
        assert (
            invalid.status_code == 400
            and invalid.json()["error"]["code"] == "scope_review_invalid_span"
        )
        published = await db_client.post(path + "/scope-reviews", json=body, headers=CSRF)
        assert published.status_code == 200, published.text
        assert await applicable() is False
        assert (
            published.json()["data"]["envelope"]["scope_facts"][0]["review_provenance"]["reviewer"]
            != "requested-reviewer"
        )
        repeated = await db_client.post(path + "/scope-reviews", json=body, headers=CSRF)
        assert repeated.json()["data"]["id"] == published.json()["data"]["id"]
        changed = deepcopy(body)
        changed["facts"][0]["review_provenance"]["reason"] = "A different review"
        immutable = await db_client.post(path + "/scope-reviews", json=changed, headers=CSRF)
        assert (
            immutable.status_code == 400
            and immutable.json()["error"]["code"] == "scope_review_immutable"
        )
        foreign = await _project_id(db_client)
        other = await db_client.post(
            f"/api/v1/projects/{foreign}/index-builds/{build}/scope-reviews",
            json=body,
            headers=CSRF,
        )
        assert other.status_code == 404
        await session.refresh(chunk)
        assert chunk.chunk_metadata == original_metadata
        row = await session.scalar(
            select(IndexScopeReview).where(
                IndexScopeReview.chunk_id == chunk_id, IndexScopeReview.build_id == uuid.UUID(build)
            )
        )
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(
                    update(IndexScopeReview)
                    .where(IndexScopeReview.id == row.id)
                    .values(created_by="tampered")
                )
        current_build = await session.get(IndexBuild, uuid.UUID(build))
        assert current_build is not None
        assert (
            await semantic_snapshot_hash(session, uuid.UUID(project), current_build)
            != before["semantic_structure_hash"]
        )
    assert (await db_client.get(f"/api/v1/projects/{project}/index-builds")).json()["data"][
        "active_build_id"
    ] == active


async def test_candidate_evaluation_is_privileged_pinned_and_runs_without_activation(
    db_client, integration_connection, captured_jobs
):
    project = await _project_id(db_client)
    document = await _upload(
        db_client, project, "candidate-rule.txt", "The policy requires two signatures for approval."
    )
    await _index_documents(db_client, integration_connection, captured_jobs, project, [document])
    build = await candidate(db_client, integration_connection, captured_jobs, project)
    active = (await db_client.get(f"/api/v1/projects/{project}/index-builds")).json()["data"][
        "active_build_id"
    ]
    base = f"/api/v1/projects/{project}/evaluations"
    dataset = await db_client.post(
        base + "/datasets",
        json={
            "name": "sealed-candidate",
            "version": "v1",
            "cases": [
                {
                    "key": "rule",
                    "kind": "citation",
                    "query": "What signatures does the policy require?",
                    "relevant_document_ids": [document],
                }
            ],
        },
    )
    assert dataset.status_code == 201, dataset.text
    body = {"dataset_id": dataset.json()["data"]["id"], "preview_index_build_id": build}
    ordinary = await db_client.post(base + "/runs", json=body)
    assert (
        ordinary.status_code == 400 and ordinary.json()["error"]["code"] == "preview_requires_admin"
    )
    wrong = await db_client.post(
        base + "/runs/captured",
        json={**body, "preview_index_build_id": str(uuid.uuid4())},
        headers=CSRF,
    )
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "preview_build_unavailable"
    not_candidate = await db_client.post(
        base + "/runs/captured", json={**body, "preview_index_build_id": active}, headers=CSRF
    )
    assert not_candidate.status_code == 400
    queued = await db_client.post(base + "/runs/captured", json=body, headers=CSRF)
    assert queued.status_code == 202, queued.text
    run = queued.json()["data"]
    assert run["index_build_id"] == run["versions"]["corpus"]["index_build_id"] == build
    assert run["config_provenance"]["preview_index_build_id"] == build
    await run_captured_evaluation_jobs(integration_connection, captured_jobs)
    completed = await db_client.get(base + "/runs/" + run["id"])
    assert completed.status_code == 200
    assert completed.json()["data"]["job_state"] == "succeeded", completed.text
    assert completed.json()["data"]["index_build_id"] == build
    assert (await db_client.get(f"/api/v1/projects/{project}/index-builds")).json()["data"][
        "active_build_id"
    ] == active


@pytest.mark.parametrize("first_build", [False, True])
async def test_observed_report_binds_actual_regular_sse_get_and_common_activation_receipt(
    db_client, integration_connection, captured_jobs, first_build
):
    project_response = await db_client.post(
        "/api/v1/projects", json={"name": "Observed empty corpus " + uuid.uuid4().hex}
    )
    project_data = project_response.json()["data"]
    project = project_data["id"]
    configured = await db_client.post(
        f"/api/v1/operator/projects/{project}/ai-config/revisions",
        json={
            "expected_active_revision_id": project_data["active_ai_config_revision_id"],
            "reason": "Isolated observed adaptive acceptance",
            "configuration": {
                "behavior": {},
                "execution": {"execution_policy": "adaptive_v1", "bounded_recovery_enabled": True},
            },
        },
        headers=CSRF,
    )
    assert configured.status_code == 201, configured.text
    active = None
    if not first_build:
        active = await candidate(db_client, integration_connection, captured_jobs, project)
        async with AsyncSession(
            bind=integration_connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            await attest_fixture_build(session, uuid.UUID(project), uuid.UUID(active))
        activated = await db_client.post(
            f"/api/v1/projects/{project}/index-builds/{active}/activate"
        )
        assert activated.status_code == 200, activated.text
    build = await candidate(db_client, integration_connection, captured_jobs, project)
    path = f"/api/v1/projects/{project}/index-builds/{build}"
    identity = (await db_client.get(path + "/acceptance-identity")).json()["data"]
    definition = {
        "project_id": project,
        "build_id": build,
        "revision": "isolated.observed.v1",
        "certification": "production",
        "case_expectations": {"missing-rule": "insufficient_evidence"},
        "repetitions": 3,
        "require_comparison": True,
    }
    defined = await db_client.post(path + "/acceptance-set", json=definition, headers=CSRF)
    assert defined.status_code == 200, defined.text
    question = "Using only the indexed corpus, what is the lunar payroll rule?"
    captures = []
    parity = []
    for b in [build] if first_build else [active, build]:
        for n in range(1, 4):
            conversation = (
                await db_client.post(f"/api/v1/projects/{project}/conversations", json={})
            ).json()["data"]["id"]
            messages_path = f"/api/v1/projects/{project}/conversations/{conversation}/messages"
            request = {
                "content": question,
                "source_scope": "indexed_only",
                **({"preview_index_build_id": build} if b == build else {}),
            }
            if b == build and n == 3:
                stream = await db_client.post(messages_path + "/stream", json=request)
                events = [
                    json.loads(v.removeprefix("data: "))
                    for v in stream.text.splitlines()
                    if v.startswith("data: ")
                ]
                done = next(v for v in events if v.get("event") == "done")
                messages = (await db_client.get(messages_path)).json()["data"]["items"]
                user = next(v for v in messages if v["role"] == "user")
                assistant = next(v for v in messages if v["id"] == done["assistant_message_id"])
                parity.extend(
                    [
                        {
                            "assistant_message_id": assistant["id"],
                            "transport": "sse",
                            "raw_message": done,
                        },
                        {
                            "assistant_message_id": assistant["id"],
                            "transport": "get",
                            "raw_message": assistant,
                        },
                    ]
                )
            else:
                response = await db_client.post(messages_path, json=request)
                assert response.status_code == 200, response.text
                user, assistant = (
                    response.json()["data"]["user_message"],
                    response.json()["data"]["assistant_message"],
                )
            assert assistant["terminal_outcome"]["outcome"] == "insufficient_evidence"
            assert assistant["claims"] == [] and assistant["grounded"] is False
            captures.append(
                {
                    "case_id": "missing-rule",
                    "repetition": n,
                    "build_id": b,
                    "user_message_id": user["id"],
                    "assistant_message_id": assistant["id"],
                    "raw_message": assistant,
                }
            )
    package = {
        "comparison_mode": "first_build" if first_build else "active_candidate",
        "compared_active_build_id": active,
        "labels": {
            "missing-rule": {
                "question": question,
                "expected": "insufficient_evidence",
                "reason": "This isolated sealed inventory contains no documents",
                "inventory_hash": identity["semantic_structure_hash"],
                "missing_requirements": ["lunar payroll rule"],
            }
        },
        "turns": captures,
        "parity": parity,
    }
    bad = deepcopy(package)
    bad["turns"][0]["raw_message"]["content"] = "Invented outcome"
    rejected = await db_client.post(path + "/acceptance-reports", json=bad, headers=CSRF)
    assert (
        rejected.status_code == 400
        and rejected.json()["error"]["code"] == "acceptance_observed_invalid"
    )
    foreign = deepcopy(package)
    foreign["turns"][0]["assistant_message_id"] = str(uuid.uuid4())
    rejected = await db_client.post(path + "/acceptance-reports", json=foreign, headers=CSRF)
    assert rejected.status_code == 400
    published = await db_client.post(path + "/acceptance-reports", json=package, headers=CSRF)
    assert published.status_code == 200, published.text
    saved = published.json()["data"]
    report = saved["report"]
    assert len(report["turns"]) == (3 if first_build else 6)
    assert len(report["cases"]) == 3
    assert report["baseline_status"] == ("absent" if first_build else "observed")
    assert report["identity"]["active_build_id"] == active
    assert report["metrics"][build]["correct_abstention_rate"] == 1
    assert report["metrics"][build]["citation_coverage_status"] == "not_applicable"
    receipt = {
        "certification": "production",
        "project_id": project,
        "build_id": build,
        "code_fingerprint": identity["code"],
        "configuration_hash": identity["configuration_hash"],
        "project_config_revision_id": identity["config_revision_id"],
        "index_configuration_hash": identity["index_configuration_hash"],
        "source_generation": identity["source_generation"],
        "corpus_fingerprint": identity["corpus_fingerprint"],
        "build_manifest_hash": identity["build_manifest_hash"],
        "semantic_structure_hash": identity["semantic_structure_hash"],
        "embedding_identity": identity["embedding_identity"],
        "acceptance_set_revision": definition["revision"],
        "acceptance_set_hash": defined.json()["data"]["definition_hash"],
        "report_hash": digest(report["cases"]),
        "cases": report["cases"],
        "compared_active_build_id": active,
        "comparison_report_hash": report["comparison_hash"],
        "observed_report_id": saved["id"],
        "observed_report_hash": saved["report_hash"],
    }
    unattested = {**receipt, "observed_report_id": str(uuid.uuid4())}
    missing = await db_client.post(path + "/acceptance", json=unattested, headers=CSRF)
    assert (
        missing.status_code == 400
        and missing.json()["error"]["code"] == "index_acceptance_observed_missing"
    )
    accepted = await db_client.post(path + "/acceptance", json=receipt, headers=CSRF)
    assert accepted.status_code == 200, accepted.text
    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        row = await session.get(IndexAcceptanceReport, uuid.UUID(saved["id"]))
        assert digest(row.report) == row.report_hash
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(
                    update(IndexAcceptanceReport)
                    .where(IndexAcceptanceReport.id == row.id)
                    .values(created_by="tampered")
                )
    final = await db_client.post(path + "/activate")
    assert final.status_code == 200, final.text
    assert (await db_client.get(f"/api/v1/projects/{project}/index-builds")).json()["data"][
        "active_build_id"
    ] == build
