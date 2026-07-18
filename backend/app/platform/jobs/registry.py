"""Job name → Taskiq task dispatch registry."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.platform.jobs.errors import JobEnqueueError
from app.platform.jobs.names import (
    CORPUS_REEMBED,
    CORPUS_REINDEX,
    DOCUMENT_DELETE,
    DOCUMENT_EMBED,
    DOCUMENT_INDEX,
    DOCUMENT_PROCESS,
    DOCUMENT_PURGE,
    EVALUATION_RUN,
    STORAGE_RECONCILE,
)


@dataclass(frozen=True, slots=True)
class JobDispatchSpec:
    """How to validate payload and enqueue a named job on Taskiq."""

    validate_payload: Callable[[dict[str, Any]], str]
    enqueue: Callable[..., Awaitable[Any]]


def _validate_job_id(payload: dict[str, Any]) -> str:
    job_id = payload.get("job_id")
    if job_id is None:
        msg = "Job payload requires job_id"
        raise JobEnqueueError(msg)
    return str(job_id)


def _build_registry() -> dict[str, JobDispatchSpec]:
    from app.worker.handlers.corpus import corpus_reembed_task, corpus_reindex_task
    from app.worker.handlers.document import document_process_task
    from app.worker.handlers.document_lifecycle import document_delete_task, document_purge_task
    from app.worker.handlers.embedding import document_embed_task
    from app.worker.handlers.evaluation import evaluation_run_task
    from app.worker.handlers.indexing import document_index_task
    from app.worker.handlers.storage_reconciliation import storage_reconcile_task

    async def enqueue_process(
        *,
        job_id: str,
        project_id: uuid.UUID,
        durable_job_id: str,
    ) -> Any:
        return await (
            document_process_task.kicker()
            .with_task_id(job_id)
            .kiq(project_id=str(project_id), job_id=durable_job_id)
        )

    async def enqueue_embed(
        *,
        job_id: str,
        project_id: uuid.UUID,
        durable_job_id: str,
    ) -> Any:
        return await (
            document_embed_task.kicker()
            .with_task_id(job_id)
            .kiq(project_id=str(project_id), job_id=durable_job_id)
        )

    async def enqueue_index(
        *,
        job_id: str,
        project_id: uuid.UUID,
        durable_job_id: str,
    ) -> Any:
        return await (
            document_index_task.kicker()
            .with_task_id(job_id)
            .kiq(project_id=str(project_id), job_id=durable_job_id)
        )

    async def enqueue_evaluation(
        *,
        job_id: str,
        project_id: uuid.UUID,
        durable_job_id: str,
    ) -> Any:
        return await (
            evaluation_run_task.kicker()
            .with_task_id(job_id)
            .kiq(project_id=str(project_id), job_id=durable_job_id)
        )

    async def enqueue_corpus_reembed(
        *, job_id: str, project_id: uuid.UUID, durable_job_id: str
    ) -> Any:
        return await (
            corpus_reembed_task.kicker()
            .with_task_id(job_id)
            .kiq(project_id=str(project_id), job_id=durable_job_id)
        )

    async def enqueue_corpus_reindex(
        *, job_id: str, project_id: uuid.UUID, durable_job_id: str
    ) -> Any:
        return await (
            corpus_reindex_task.kicker()
            .with_task_id(job_id)
            .kiq(project_id=str(project_id), job_id=durable_job_id)
        )

    async def enqueue_document_delete(
        *, job_id: str, project_id: uuid.UUID, durable_job_id: str
    ) -> Any:
        return await (
            document_delete_task.kicker()
            .with_task_id(job_id)
            .kiq(project_id=str(project_id), job_id=durable_job_id)
        )

    async def enqueue_document_purge(
        *, job_id: str, project_id: uuid.UUID, durable_job_id: str
    ) -> Any:
        return await (
            document_purge_task.kicker()
            .with_task_id(job_id)
            .kiq(project_id=str(project_id), job_id=durable_job_id)
        )

    async def enqueue_storage_reconcile(
        *, job_id: str, project_id: uuid.UUID, durable_job_id: str
    ) -> Any:
        return await (
            storage_reconcile_task.kicker()
            .with_task_id(job_id)
            .kiq(project_id=str(project_id), job_id=durable_job_id)
        )

    return {
        DOCUMENT_PROCESS: JobDispatchSpec(
            validate_payload=_validate_job_id,
            enqueue=enqueue_process,
        ),
        DOCUMENT_EMBED: JobDispatchSpec(
            validate_payload=_validate_job_id,
            enqueue=enqueue_embed,
        ),
        DOCUMENT_INDEX: JobDispatchSpec(
            validate_payload=_validate_job_id,
            enqueue=enqueue_index,
        ),
        EVALUATION_RUN: JobDispatchSpec(
            validate_payload=_validate_job_id,
            enqueue=enqueue_evaluation,
        ),
        CORPUS_REEMBED: JobDispatchSpec(_validate_job_id, enqueue_corpus_reembed),
        CORPUS_REINDEX: JobDispatchSpec(_validate_job_id, enqueue_corpus_reindex),
        DOCUMENT_DELETE: JobDispatchSpec(_validate_job_id, enqueue_document_delete),
        DOCUMENT_PURGE: JobDispatchSpec(_validate_job_id, enqueue_document_purge),
        STORAGE_RECONCILE: JobDispatchSpec(_validate_job_id, enqueue_storage_reconcile),
    }


_REGISTRY: dict[str, JobDispatchSpec] | None = None


def get_job_registry() -> dict[str, JobDispatchSpec]:
    """Lazy registry so worker handler modules load after registration."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY
