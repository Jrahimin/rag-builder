"""Project-scoped evaluation dataset and durable-run API integration tests."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.platform.jobs.contracts import JobDefinition
from tests.integration.knowledge_helpers import (
    run_captured_document_jobs,
    run_captured_embed_jobs,
    run_captured_evaluation_jobs,
    run_captured_index_jobs,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _project(client: AsyncClient, suffix: str) -> str:
    response = await client.post(
        "/api/v1/projects",
        json={"name": f"evaluation project {suffix} {uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 201
    return response.json()["data"]["id"]


def _dataset_payload() -> dict:
    return {
        "name": "representative",
        "version": "1.0.0",
        "cases": [
            {
                "key": "citation",
                "kind": "citation",
                "query": "What is the policy?",
                "relevant_chunk_ids": [str(uuid.uuid4())],
                "expected_answer_tokens": ["policy"],
            },
            {
                "key": "no-answer",
                "kind": "no_answer",
                "query": "What is the lunar payroll rule?",
                "expected_no_answer": True,
            },
        ],
    }


async def test_dataset_and_run_capture_reproducible_versions(db_client: AsyncClient) -> None:
    project_id = await _project(db_client, "one")
    dataset = await db_client.post(
        f"/api/v1/projects/{project_id}/evaluations/datasets",
        json=_dataset_payload(),
    )
    assert dataset.status_code == 201
    dataset_data = dataset.json()["data"]
    assert len(dataset_data["dataset_hash"]) == 64

    duplicate = await db_client.post(
        f"/api/v1/projects/{project_id}/evaluations/datasets",
        json=_dataset_payload(),
    )
    assert duplicate.status_code == 409

    queued = await db_client.post(
        f"/api/v1/projects/{project_id}/evaluations/runs",
        json={"dataset_id": dataset_data["id"], "top_k": 5},
    )
    assert queued.status_code == 202
    run = queued.json()["data"]
    assert run["job_state"] == "queued"
    assert len(run["configuration_hash"]) == 64
    assert run["versions"]["dataset"]["hash"] == dataset_data["dataset_hash"]
    assert run["versions"]["prompt_version"] == "v2"
    assert run["versions"]["chunking"]["chunker_version"] == "2.0.0"
    assert len(run["versions"]["corpus"]["fingerprint"]) == 64
    assert run["versions"]["corpus"]["indexed_chunk_count"] == 0

    newer_payload = _dataset_payload()
    newer_payload["version"] = "2.0.0"
    newer_dataset = await db_client.post(
        f"/api/v1/projects/{project_id}/evaluations/datasets",
        json=newer_payload,
    )
    assert newer_dataset.status_code == 201

    summary = await db_client.get(
        f"/api/v1/projects/{project_id}/evaluations/quality"
    )
    assert summary.status_code == 200
    assert summary.json()["data"]["last_run"]["id"] == run["id"]
    assert summary.json()["data"]["dataset"]["id"] == dataset_data["id"]


async def test_evaluation_runs_are_project_scoped(db_client: AsyncClient) -> None:
    first_project = await _project(db_client, "first")
    second_project = await _project(db_client, "second")
    dataset = await db_client.post(
        f"/api/v1/projects/{first_project}/evaluations/datasets",
        json=_dataset_payload(),
    )
    queued = await db_client.post(
        f"/api/v1/projects/{first_project}/evaluations/runs",
        json={"dataset_id": dataset.json()["data"]["id"]},
    )
    run_id = queued.json()["data"]["id"]

    cross_project = await db_client.get(
        f"/api/v1/projects/{second_project}/evaluations/runs/{run_id}"
    )
    assert cross_project.status_code == 404


async def test_durable_runner_persists_metrics_claims_and_refusal(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
) -> None:
    project_id = await _project(db_client, "vertical")
    upload = await db_client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={
            "file": (
                "quality-policy.txt",
                (
                    "Cobalt escalation matrix assigns urgent incidents to Reliability. "
                    "Privacy reviews require policy approval. "
                    "গ্রাহক রপ্তানির অনুমোদন requires approval. "
                    "Credentials rotate every ninety days."
                ).encode(),
                "text/plain",
            )
        },
    )
    assert upload.status_code == 201
    document_id = upload.json()["data"]["id"]
    await run_captured_document_jobs(integration_connection, captured_jobs)
    chunk_id = (
        await integration_connection.execute(
            text(
                "SELECT id FROM document_chunks "
                "WHERE project_id = :project_id AND document_id = :document_id"
            ),
            {"project_id": uuid.UUID(project_id), "document_id": uuid.UUID(document_id)},
        )
    ).scalar_one()
    await integration_connection.execute(
        text(
            "UPDATE document_chunks "
            "SET metadata = metadata || '{\"source\": \"policy\"}'::jsonb "
            "WHERE id = :chunk_id"
        ),
        {"chunk_id": chunk_id},
    )
    embed = await db_client.post(
        f"/api/v1/projects/{project_id}/documents/{document_id}/embed"
    )
    assert embed.status_code == 200
    await run_captured_embed_jobs(integration_connection, captured_jobs)
    index = await db_client.post(
        f"/api/v1/projects/{project_id}/documents/{document_id}/index"
    )
    assert index.status_code == 200
    await run_captured_index_jobs(integration_connection, captured_jobs)

    dataset = await db_client.post(
        f"/api/v1/projects/{project_id}/evaluations/datasets",
        json={
            "name": "vertical-slice",
            "version": "1.0.0",
            "cases": [
                {
                    "key": "exact",
                    "kind": "exact_token",
                    "query": "cobalt escalation matrix",
                    "relevant_chunk_ids": [str(chunk_id)],
                    "expected_answer_tokens": ["cobalt"],
                },
                {
                    "key": "paraphrase",
                    "kind": "paraphrase",
                    "query": "Who handles urgent incidents?",
                    "relevant_chunk_ids": [str(chunk_id)],
                },
                {
                    "key": "filter",
                    "kind": "metadata_filter",
                    "query": "privacy reviews",
                    "relevant_document_ids": [document_id],
                    "metadata_filter": {"source": "policy"},
                },
                {
                    "key": "multilingual",
                    "kind": "multilingual",
                    "query": "গ্রাহক রপ্তানির অনুমোদন",
                    "relevant_chunk_ids": [str(chunk_id)],
                },
                {
                    "key": "no-answer",
                    "kind": "no_answer",
                    "query": "What is the lunar payroll rule?",
                    "expected_no_answer": True,
                },
                {
                    "key": "citation",
                    "kind": "citation",
                    "query": "How often do credentials rotate?",
                    "relevant_chunk_ids": [str(chunk_id)],
                    "expected_answer_tokens": ["credentials"],
                },
            ],
        },
    )
    assert dataset.status_code == 201
    queued = await db_client.post(
        f"/api/v1/projects/{project_id}/evaluations/runs",
        json={"dataset_id": dataset.json()["data"]["id"], "top_k": 5},
    )
    assert queued.status_code == 202
    run_id = queued.json()["data"]["id"]

    await run_captured_evaluation_jobs(integration_connection, captured_jobs)

    response = await db_client.get(
        f"/api/v1/projects/{project_id}/evaluations/runs/{run_id}"
    )
    assert response.status_code == 200
    run = response.json()["data"]
    assert run["job_state"] == "succeeded"
    assert run["completed_at"] is not None
    assert set(run["metrics"]) == {
        "semantic",
        "hybrid",
        "reranked_lexical",
        "reranked_embedding",
        "reranked_embedding_max",
    }
    assert len(run["case_results"]) == 30
    assert run["versions"]["corpus"]["indexed_chunk_count"] == 1
    exact = next(
        item
        for item in run["case_results"]
        if item["case_key"] == "exact" and item["profile"] == "reranked_lexical"
    )
    assert exact["grounded"] is True
    assert exact["claims"]
    assert exact["claims"][0]["evidence"][0]["chunk_id"] == str(chunk_id)
    assert exact["citation_coverage"] == 0.0
    no_answer = next(
        item
        for item in run["case_results"]
        if item["case_key"] == "no-answer" and item["profile"] == "reranked_lexical"
    )
    assert no_answer["insufficient_evidence_reason"] is not None
    assert no_answer["claims"] == []
    assert run["reranker_comparison"]["recommended_profile"] is None
