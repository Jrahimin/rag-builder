# ruff: noqa: T201
"""Read-only deployment diagnostics with secret-safe output."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.core.auth_config_validation import validate_auth_config
from app.core.config import (
    EmbeddingBackend,
    JobQueueBackend,
    LLMBackend,
    RerankerBackend,
    Settings,
    get_settings,
)
from app.core.runtime_validation import validate_runtime_config
from app.platform.db.session import Database
from app.platform.infra.connectivity.redis import RedisConnectivity
from app.platform.providers.implementations.storage_factory import create_storage_provider


@dataclass(frozen=True, slots=True)
class DoctorResult:
    name: str
    state: str
    detail: str
    critical: bool = True


async def _run_check(
    name: str,
    operation: Callable[[], Awaitable[None]],
    *,
    action: str,
) -> DoctorResult:
    try:
        await operation()
    except Exception as exc:
        return DoctorResult(
            name=name,
            state="FAIL",
            detail=f"{type(exc).__name__}: check failed. {action}",
        )
    return DoctorResult(name=name, state="PASS", detail="ok")


def _configuration_results(settings: Settings) -> list[DoctorResult]:
    validate_runtime_config(settings)
    validate_auth_config(settings)
    results = [DoctorResult("configuration", "PASS", "settings validated")]
    if settings.embedding.backend is EmbeddingBackend.HASH:
        results.append(
            DoctorResult(
                "embedding_provider",
                "WARN",
                "hash embeddings are deterministic development/test tooling",
                critical=False,
            )
        )
    else:
        results.append(
            DoctorResult(
                "embedding_provider",
                "PASS",
                f"{settings.embedding.backend.value}/{settings.embedding.model}; "
                f"dimensions={settings.embedding.dimensions}",
                critical=False,
            )
        )
    if settings.llm.backend is LLMBackend.ECHO:
        results.append(
            DoctorResult(
                "llm_provider",
                "WARN",
                "echo LLM is development/test tooling",
                critical=False,
            )
        )
    else:
        results.append(
            DoctorResult(
                "llm_provider",
                "PASS",
                f"{settings.llm.backend.value}/{settings.llm.model}",
                critical=False,
            )
        )
    reranker_state = (
        "WARN"
        if settings.retrieval.rerank_enabled
        and settings.retrieval.reranker_backend is RerankerBackend.NOOP
        else "PASS"
    )
    results.append(
        DoctorResult(
            "reranker_provider",
            reranker_state,
            (
                "noop reranker selected while reranking is enabled"
                if reranker_state == "WARN"
                else settings.retrieval.reranker_backend.value
            ),
            critical=False,
        )
    )
    results.append(
        DoctorResult(
            "worker_broker",
            "PASS" if settings.jobs.backend is JobQueueBackend.TASKIQ else "WARN",
            (
                "Taskiq over configured Redis; connectivity checked separately"
                if settings.jobs.backend is JobQueueBackend.TASKIQ
                else "inline execution is intended only for development/testing"
            ),
            critical=False,
        )
    )
    return results


async def run_doctor(settings: Settings) -> int:
    """Run bounded local dependency checks without invoking AI providers."""
    database = Database(settings)
    redis = RedisConnectivity(settings)
    storage = create_storage_provider(settings)
    results = _configuration_results(settings)
    try:
        results.extend(
            await asyncio.gather(
                _run_check(
                    "postgresql",
                    database.check_connection,
                    action="Verify database host, port, credentials, and database name.",
                ),
                _run_check(
                    "migrations",
                    database.check_migrations,
                    action="Run `alembic upgrade head`.",
                ),
                _run_check(
                    "pgvector",
                    database.check_pgvector,
                    action="Enable pgvector and verify the configured embedding dimension.",
                ),
                _run_check(
                    "redis",
                    redis.check,
                    action="Verify Redis/broker connectivity and credentials.",
                ),
                _run_check(
                    "object_storage",
                    storage.check,
                    action="Verify the local root or configured bucket and credentials.",
                ),
            )
        )
    finally:
        await redis.dispose()
        await database.dispose()

    for result in results:
        print(f"[{result.state}] {result.name}: {result.detail}")
    failures = [result for result in results if result.critical and result.state == "FAIL"]
    print(
        f"Doctor completed: {len(failures)} critical failure(s), "
        f"{sum(result.state == 'WARN' for result in results)} warning(s)."
    )
    return 1 if failures else 0


def main() -> int:
    """Load configuration and return a shell-friendly diagnostic exit code."""
    try:
        settings = get_settings()
        return asyncio.run(run_doctor(settings))
    except Exception as exc:
        print(f"[FAIL] configuration: {type(exc).__name__}: settings are invalid.")
        return 1
