"""Bounded core-readiness and optional AI-capability checks."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import perf_counter

from app.core.config import MalwareScannerBackend, OcrBackend, Settings
from app.core.logging import get_logger
from app.platform.db.session import Database, PgVectorUnavailableError
from app.platform.infra.connectivity.redis import RedisConnectivity
from app.platform.providers.contracts.llm import ChatMessage, ChatRole
from app.platform.providers.contracts.reranker import RerankCandidate, RerankRequest
from app.platform.providers.contracts.storage import BaseStorageProvider
from app.platform.providers.implementations.embedding_factory import create_embedding_provider
from app.platform.providers.implementations.llm_factory import create_llm_provider
from app.platform.providers.implementations.ocr_factory import get_ocr_provider
from app.platform.providers.implementations.reranker_factory import create_reranker_provider
from app.platform.system.schemas import DependencyHealth, DependencyState, PreflightStatus

log = get_logger(__name__)


class StartupPreflightError(RuntimeError):
    """Raised when a strict production startup capability check fails."""


class StartupPreflightService:
    """Run dependency and provider checks once during application startup."""

    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        redis: RedisConnectivity,
        storage: BaseStorageProvider,
    ) -> None:
        self._settings = settings
        self._database = database
        self._redis = redis
        self._storage = storage

    async def run_core(self) -> PreflightStatus:
        """Fail only when the services required to operate the core API are down."""
        dependency_timeout = self._settings.runtime.dependency_timeout_seconds
        checks = await asyncio.wait_for(
            asyncio.gather(
                self._check(
                    "postgresql",
                    self._database.check,
                    check_timeout=dependency_timeout,
                    action="Verify PostgreSQL, pgvector, migrations, and embedding dimensions.",
                ),
                self._check(
                    "redis",
                    self._redis.check,
                    check_timeout=dependency_timeout,
                    action="Verify Redis connectivity and credentials.",
                ),
                self._check(
                    "object_storage",
                    self._storage.check,
                    check_timeout=dependency_timeout,
                    action="Verify object-storage credentials and bucket access.",
                ),
                self._check(
                    "malware_scanner",
                    self._check_malware_scanner,
                    check_timeout=dependency_timeout,
                    action="Verify the production ClamAV service is reachable.",
                ),
            ),
            timeout=self._settings.runtime.startup_timeout_seconds,
        )
        checked_at = datetime.now(UTC)
        healthy = all(
            check.state in {DependencyState.OK, DependencyState.SKIPPED} for check in checks
        )
        result = PreflightStatus(
            status="ready" if healthy else "not_ready",
            profile=self._settings.runtime.profile.value,
            checked_at=checked_at,
            checks=[*checks, *self._pending_provider_checks()],
        )
        if self._settings.app.is_production and not healthy:
            failed = ", ".join(
                check.name for check in checks if check.state == DependencyState.DOWN
            )
            raise StartupPreflightError(f"Production startup preflight failed: {failed}")
        return result

    async def run_provider_checks(self, core: PreflightStatus) -> PreflightStatus:
        """Return cached capability health without changing core readiness.

        Provider connectivity is deliberately not a startup gate: the API can
        still serve authentication, projects, operator status, and persisted
        document state while an external AI endpoint recovers.
        """
        provider_timeout = self._settings.runtime.provider_timeout_seconds
        checks = await asyncio.gather(
            self._check(
                "embedding_provider",
                self._check_embedding,
                check_timeout=provider_timeout,
                action="Verify embedding endpoint, model, credentials, and dimensions.",
                failure_state=DependencyState.DEGRADED,
            ),
            self._check(
                "llm_provider",
                self._check_llm,
                check_timeout=provider_timeout,
                action="Verify the configured chat endpoint, model, and credentials.",
                failure_state=DependencyState.DEGRADED,
            ),
            self._check(
                "reranker_provider",
                self._check_reranker,
                check_timeout=provider_timeout,
                action="Verify the configured reranker path.",
                failure_state=DependencyState.DEGRADED,
            ),
            self._check_ocr(failure_state=DependencyState.DEGRADED),
        )
        core_checks = [check for check in core.checks if not check.name.endswith("_provider")]
        capability_degraded = any(check.state is DependencyState.DEGRADED for check in checks)
        return PreflightStatus(
            status="degraded" if core.status == "ready" and capability_degraded else core.status,
            profile=core.profile,
            checked_at=datetime.now(UTC),
            checks=[*core_checks, *checks],
        )

    async def run(self) -> PreflightStatus:
        """Run all checks for callers that explicitly need a complete report."""
        return await self.run_provider_checks(await self.run_core())

    async def _check(
        self,
        name: str,
        operation: Callable[[], Awaitable[None]],
        *,
        check_timeout: float,
        action: str,
        failure_state: DependencyState = DependencyState.DOWN,
    ) -> DependencyHealth:
        started = perf_counter()
        try:
            await asyncio.wait_for(operation(), timeout=check_timeout)
        except Exception as exc:
            latency = round((perf_counter() - started) * 1000, 2)
            error_context = getattr(exc, "context", None)
            log.warning(
                "startup_preflight_failed",
                check=name,
                error_type=type(exc).__name__,
                error=str(exc),
                error_context=error_context if isinstance(error_context, dict) else None,
            )
            return DependencyHealth(
                name=name,
                state=failure_state,
                detail=_safe_failure_detail(exc),
                action=action,
                latency_ms=latency,
                checked_at=datetime.now(UTC),
            )
        return DependencyHealth(
            name=name,
            state=DependencyState.OK,
            latency_ms=round((perf_counter() - started) * 1000, 2),
            checked_at=datetime.now(UTC),
        )

    async def _check_embedding(self) -> None:
        provider = create_embedding_provider(self._settings)
        result = await provider.embed_texts(["runtime capability preflight"])
        expected = self._settings.embedding.dimensions
        if result.dimensions != expected or any(
            len(vector) != expected for vector in result.vectors
        ):
            msg = (
                f"Embedding dimension mismatch: configured {expected}, returned {result.dimensions}"
            )
            raise RuntimeError(msg)

    async def _check_llm(self) -> None:
        provider = create_llm_provider(self._settings)
        await provider.generate(
            [ChatMessage(role=ChatRole.USER, content="Reply OK")],
            max_tokens=20,
        )

    async def _check_reranker(self) -> None:
        if not self._settings.retrieval.rerank_enabled:
            return
        provider = create_reranker_provider(self._settings)
        await provider.rerank(
            RerankRequest(
                query="runtime",
                candidates=[
                    RerankCandidate(
                        chunk_id=uuid.UUID(int=0),
                        text="runtime capability preflight",
                        source_score=1.0,
                    )
                ],
                top_n=1,
            )
        )

    async def _check_malware_scanner(self) -> None:
        if self._settings.malware_scan.backend is not MalwareScannerBackend.CLAMAV:
            return
        reader, writer = await asyncio.open_connection(
            self._settings.malware_scan.host,
            self._settings.malware_scan.port,
        )
        try:
            writer.write(b"zPING" + bytes(1))
            await writer.drain()
            response = await reader.readuntil(bytes(1))
            if response != b"PONG" + bytes(1):
                raise RuntimeError("ClamAV did not acknowledge the health ping.")
        finally:
            writer.close()
            await writer.wait_closed()

    async def _check_ocr(
        self,
        *,
        failure_state: DependencyState = DependencyState.DOWN,
    ) -> DependencyHealth:
        if not self._settings.ocr.enabled:
            return DependencyHealth(
                name="ocr_provider",
                state=DependencyState.SKIPPED,
                detail="OCR is disabled by deployment configuration.",
                checked_at=datetime.now(UTC),
            )
        if (
            self._settings.ocr.backend is OcrBackend.NOOP
            and self._settings.ocr.bangla_backend is OcrBackend.NOOP
        ):
            return DependencyHealth(
                name="ocr_provider",
                state=failure_state,
                detail="Enabled OCR resolved to the noop provider.",
                action="Configure a real OCR backend or disable OCR.",
                checked_at=datetime.now(UTC),
            )

        async def initialize() -> None:
            # Warm the same process-scoped provider pool used by document
            # processing.  Constructing a provider directly here made the
            # startup check discard the initialized model, so the first real
            # OCR request could initialize it again (and appear unhealthy).
            languages: list[str] = []
            if self._settings.ocr.backend is not OcrBackend.NOOP:
                languages.append(self._settings.ocr.lang)
            if self._settings.ocr.bangla_backend is not OcrBackend.NOOP:
                languages.append("bn")
            for language in dict.fromkeys(languages):
                await asyncio.to_thread(
                    get_ocr_provider,
                    lang=language,
                    settings=self._settings,
                )

        return await self._check(
            "ocr_provider",
            initialize,
            check_timeout=self._settings.runtime.provider_timeout_seconds,
            action="Verify OCR dependencies, model availability, language, and device settings.",
            failure_state=failure_state,
        )

    @staticmethod
    def _pending_provider_checks() -> list[DependencyHealth]:
        checked_at = datetime.now(UTC)
        return [
            DependencyHealth(
                name=name,
                state=DependencyState.DEGRADED,
                detail="Capability check is running in the background.",
                action="Check the operator readiness view after startup.",
                checked_at=checked_at,
            )
            for name in (
                "embedding_provider",
                "llm_provider",
                "reranker_provider",
                "ocr_provider",
            )
        ]


def _safe_failure_detail(exc: Exception) -> str:
    if isinstance(exc, PgVectorUnavailableError):
        return str(exc)
    if isinstance(exc, TimeoutError):
        return "Capability check timed out."
    return f"{type(exc).__name__}: capability check failed; see structured startup logs."
