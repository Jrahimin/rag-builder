"""Job name → Taskiq task dispatch registry."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.platform.jobs.contracts import RetryPolicy
from app.platform.jobs.errors import JobEnqueueError
from app.platform.jobs.names import (
    DOCUMENT_EMBED,
    DOCUMENT_INDEX,
    DOCUMENT_PROCESS,
)


@dataclass(frozen=True, slots=True)
class JobDispatchSpec:
    """How to validate payload and enqueue a named job on Taskiq."""

    validate_payload: Callable[[dict[str, Any]], tuple[str, str]]
    enqueue: Callable[..., Awaitable[Any]]


def _retry_labels(retry: RetryPolicy) -> dict[str, Any]:
    """Translate the platform retry policy to Taskiq SmartRetryMiddleware labels."""
    return {
        "retry_on_error": True,
        "max_retries": retry.max_attempts,
        "delay": retry.initial_delay_seconds,
    }


def _validate_document_id(payload: dict[str, Any]) -> tuple[str, str]:
    document_id = payload.get("document_id")
    if document_id is None:
        msg = "Job payload requires document_id"
        raise JobEnqueueError(msg)
    return str(document_id), "document_id"


def _build_registry() -> dict[str, JobDispatchSpec]:
    from app.worker.handlers.document import document_process_task
    from app.worker.handlers.embedding import document_embed_task
    from app.worker.handlers.indexing import document_index_task

    async def enqueue_process(
        *,
        job_id: str,
        project_id: uuid.UUID,
        document_id: str,
        retry: RetryPolicy,
    ) -> Any:
        return await (
            document_process_task.kicker()
            .with_task_id(job_id)
            .with_labels(**_retry_labels(retry))
            .kiq(project_id=str(project_id), document_id=document_id)
        )

    async def enqueue_embed(
        *,
        job_id: str,
        project_id: uuid.UUID,
        document_id: str,
        retry: RetryPolicy,
    ) -> Any:
        return await (
            document_embed_task.kicker()
            .with_task_id(job_id)
            .with_labels(**_retry_labels(retry))
            .kiq(project_id=str(project_id), document_id=document_id)
        )

    async def enqueue_index(
        *,
        job_id: str,
        project_id: uuid.UUID,
        document_id: str,
        retry: RetryPolicy,
    ) -> Any:
        return await (
            document_index_task.kicker()
            .with_task_id(job_id)
            .with_labels(**_retry_labels(retry))
            .kiq(project_id=str(project_id), document_id=document_id)
        )

    return {
        DOCUMENT_PROCESS: JobDispatchSpec(
            validate_payload=_validate_document_id,
            enqueue=enqueue_process,
        ),
        DOCUMENT_EMBED: JobDispatchSpec(
            validate_payload=_validate_document_id,
            enqueue=enqueue_embed,
        ),
        DOCUMENT_INDEX: JobDispatchSpec(
            validate_payload=_validate_document_id,
            enqueue=enqueue_index,
        ),
    }


_REGISTRY: dict[str, JobDispatchSpec] | None = None


def get_job_registry() -> dict[str, JobDispatchSpec]:
    """Lazy registry so worker handler modules load after registration."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY
