"""Lightweight Prometheus-compatible operational metrics endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from app.dependencies.auth import require_admin_api_key
from app.dependencies.operations import OperatorServiceDep
from app.modules.operations.schemas.operator import MetricsSnapshot

router = APIRouter(tags=["system"], dependencies=[Depends(require_admin_api_key)])


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    summary="Prometheus-compatible operational metrics",
)
async def metrics(service: OperatorServiceDep) -> PlainTextResponse:
    snapshot = await service.metrics()
    return PlainTextResponse(_render_metrics(snapshot), media_type="text/plain; version=0.0.4")


def _render_metrics(snapshot: MetricsSnapshot) -> str:
    lines = [
        "# HELP ape_jobs_total Durable jobs by state.",
        "# TYPE ape_jobs_total gauge",
    ]
    for state, count in sorted(snapshot.jobs.by_state.items()):
        lines.append(f'ape_jobs_total{{state="{state}"}} {count}')
    lines.extend(
        [
            "# TYPE ape_job_failures_24h gauge",
            f"ape_job_failures_24h {snapshot.jobs.failures_24h}",
            "# TYPE ape_job_retry_attempts_total gauge",
            f"ape_job_retry_attempts_total {snapshot.jobs.retry_attempts}",
            "# TYPE ape_job_pending_dispatches gauge",
            f"ape_job_pending_dispatches {snapshot.jobs.pending_dispatches}",
            "# TYPE ape_corpus_documents gauge",
            f"ape_corpus_documents {snapshot.corpus.documents}",
            "# TYPE ape_corpus_chunks gauge",
            f"ape_corpus_chunks {snapshot.corpus.chunks}",
            "# TYPE ape_storage_bytes gauge",
            f"ape_storage_bytes {snapshot.corpus.storage_bytes}",
            "# TYPE ape_llm_input_tokens_total gauge",
            f"ape_llm_input_tokens_total {snapshot.token_usage.input_tokens}",
            "# TYPE ape_llm_output_tokens_total gauge",
            f"ape_llm_output_tokens_total {snapshot.token_usage.output_tokens}",
            "# TYPE ape_embedding_set_version gauge",
            f"ape_embedding_set_version {snapshot.active_embedding_set_version}",
        ]
    )
    if snapshot.jobs.oldest_queue_age_seconds is not None:
        lines.extend(
            [
                "# TYPE ape_oldest_queue_age_seconds gauge",
                f"ape_oldest_queue_age_seconds {snapshot.jobs.oldest_queue_age_seconds}",
            ]
        )
    if snapshot.retrieval_latency.average_ms is not None:
        lines.append(
            f"ape_retrieval_latency_milliseconds_avg {snapshot.retrieval_latency.average_ms}"
        )
    if snapshot.generation_latency.average_ms is not None:
        lines.append(
            f"ape_generation_latency_milliseconds_avg {snapshot.generation_latency.average_ms}"
        )
    return "\n".join(lines) + "\n"
