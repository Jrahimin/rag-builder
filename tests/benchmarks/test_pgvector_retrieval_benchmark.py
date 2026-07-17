"""Opt-in end-to-end pgvector ingest, latency, and recall benchmark."""

from __future__ import annotations

import json
import os
import statistics
import time
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection
from tests.integration.knowledge_helpers import (
    run_captured_document_jobs,
    run_captured_embed_jobs,
    run_captured_index_jobs,
)

from app.platform.jobs.contracts import JobDefinition

pytestmark = [
    pytest.mark.skipif(
        os.getenv("APE_RUN_PGVECTOR_BENCHMARKS", "false").lower() != "true",
        reason="Set APE_RUN_PGVECTOR_BENCHMARKS=true to run retrieval benchmarks",
    ),
    pytest.mark.integration,
    pytest.mark.benchmark,
    pytest.mark.asyncio,
]

_CORPUS_PATH = Path(__file__).with_name("pgvector_corpus.json")


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[index]


async def _create_project(client: AsyncClient, name: str) -> str:
    response = await client.post(
        "/api/v1/projects",
        json={"name": f"pgvector benchmark {name} {uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 201
    return response.json()["data"]["id"]


async def test_pgvector_retrieval_benchmark(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list[JobDefinition],
    record_property,
) -> None:
    corpus = json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))
    multiplier = max(int(os.getenv("APE_BENCHMARK_CORPUS_MULTIPLIER", "1")), 1)
    repeat_queries = max(int(os.getenv("APE_BENCHMARK_QUERY_REPEATS", "5")), 1)
    project_ids = {
        name: await _create_project(db_client, name) for name in {row["project"] for row in corpus}
    }
    indexed: list[dict[str, str]] = []
    index_latencies: list[float] = []

    ingest_started = time.perf_counter()
    for copy_index in range(multiplier):
        for row in corpus:
            project_id = project_ids[row["project"]]
            content_text = f"{row['text']} Corpus copy {copy_index}."
            content = content_text.encode()
            upload = await db_client.post(
                f"/api/v1/projects/{project_id}/documents",
                files={"file": (f"{row['topic']}-{copy_index}.txt", content, "text/plain")},
            )
            assert upload.status_code == 201
            document_id = upload.json()["data"]["id"]
            await run_captured_document_jobs(integration_connection, captured_jobs)
            await db_client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/embed")
            await run_captured_embed_jobs(integration_connection, captured_jobs)
            index_started = time.perf_counter()
            await db_client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/index")
            await run_captured_index_jobs(integration_connection, captured_jobs)
            index_latencies.append((time.perf_counter() - index_started) * 1000)
            indexed.append(
                {
                    "project_id": project_id,
                    "document_id": document_id,
                    "topic": row["topic"],
                    "query": content_text,
                }
            )
    ingest_seconds = time.perf_counter() - ingest_started

    semantic_latencies: list[float] = []
    hybrid_latencies: list[float] = []
    semantic_matches = 0
    filtered_matches = 0
    query_count = 0
    for item in indexed:
        query = item["query"]
        for _ in range(repeat_queries):
            semantic_started = time.perf_counter()
            semantic = await db_client.post(
                f"/api/v1/projects/{item['project_id']}/search",
                json={"query": query, "top_k": 5, "strategy": "semantic"},
            )
            semantic_latencies.append((time.perf_counter() - semantic_started) * 1000)
            semantic_results = semantic.json()["data"]["results"]
            semantic_matches += any(
                item["topic"] in result["content"].lower() for result in semantic_results
            )

            filtered = await db_client.post(
                f"/api/v1/projects/{item['project_id']}/search",
                json={"query": query, "top_k": 5, "document_id": item["document_id"]},
            )
            filtered_matches += any(
                result["document_id"] == item["document_id"]
                for result in filtered.json()["data"]["results"]
            )

            hybrid_started = time.perf_counter()
            hybrid = await db_client.post(
                f"/api/v1/projects/{item['project_id']}/search",
                json={"query": query, "top_k": 5, "strategy": "hybrid", "rerank": False},
            )
            assert hybrid.status_code == 200
            hybrid_latencies.append((time.perf_counter() - hybrid_started) * 1000)
            query_count += 1

    metrics = {
        "documents": len(indexed),
        "ingest_documents_per_second": len(indexed) / ingest_seconds,
        "index_build_p95_ms": _percentile(index_latencies, 0.95),
        "semantic_p50_ms": statistics.median(semantic_latencies),
        "semantic_p95_ms": _percentile(semantic_latencies, 0.95),
        "hybrid_p95_ms": _percentile(hybrid_latencies, 0.95),
        "recall_at_5": semantic_matches / query_count,
        "filtered_recall_at_5": filtered_matches / query_count,
    }
    for name, value in metrics.items():
        record_property(name, value)

    assert metrics["recall_at_5"] >= float(os.getenv("APE_BENCHMARK_MIN_RECALL", "0.90"))
    assert metrics["filtered_recall_at_5"] >= float(
        os.getenv("APE_BENCHMARK_MIN_FILTERED_RECALL", "0.95")
    )
    assert metrics["semantic_p95_ms"] <= float(
        os.getenv("APE_BENCHMARK_MAX_SEMANTIC_P95_MS", "500")
    )
    assert metrics["index_build_p95_ms"] <= float(
        os.getenv("APE_BENCHMARK_MAX_INDEX_P95_MS", "1000")
    )
    assert metrics["hybrid_p95_ms"] <= float(os.getenv("APE_BENCHMARK_MAX_HYBRID_P95_MS", "750"))
