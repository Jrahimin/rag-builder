"""Independent PostgreSQL transactions for cached vectors and billing admission."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.chunk_embedding import EMBEDDING_SCHEMA_VERSION, ChunkEmbedding
from app.models.index_build import IndexBuild, IndexBuildState
from app.models.provider_work import EmbeddingCache, ProviderUsageAttempt
from app.platform.db.advisory_lock import acquire_project_stage_lock
from app.platform.providers.provider_work import ProviderBudgetError, ProviderWorkScope, micro_usd


class ProviderWorkRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    @asynccontextmanager
    async def cache_session(self, project_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session, session.begin():
            await acquire_project_stage_lock(
                session, project_id=project_id, stage="embedding-cache"
            )
            # Expiry cleanup is bounded to this project's data and runs only during cache work.
            await session.execute(
                delete(EmbeddingCache).where(
                    EmbeddingCache.project_id == project_id, EmbeddingCache.expires_at <= func.now()
                )
            )
            yield session

    async def vectors(
        self, session: AsyncSession, project_id: uuid.UUID, keys: list[str]
    ) -> dict[str, list[float]]:
        rows = await session.execute(
            select(EmbeddingCache.cache_key, EmbeddingCache.vector).where(
                EmbeddingCache.project_id == project_id,
                EmbeddingCache.cache_key.in_(keys),
                EmbeddingCache.expires_at > func.now(),
            )
        )
        return {key: list(vector) for key, vector in rows}

    async def seed_vectors(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        identity: dict[str, Any],
        inputs: dict[str, str],
    ) -> dict[str, list[float]]:
        if identity["purpose"] != "document" or identity["namespace"] != "https://api.cohere.com":
            return {}
        # Existing sealed rows prove DOCUMENT purpose; old adapters without endpoint provenance
        # are only reusable for the canonical Cohere endpoint and exact vector identity.
        rows = await session.execute(
            select(ChunkEmbedding.input_content_hash, ChunkEmbedding.embedding)
            .join(IndexBuild, IndexBuild.id == ChunkEmbedding.index_build_id)
            .where(
                ChunkEmbedding.project_id == project_id,
                IndexBuild.project_id == project_id,
                IndexBuild.state.in_(
                    [IndexBuildState.ACTIVE, IndexBuildState.VALIDATED, IndexBuildState.RETAINED]
                ),
                ChunkEmbedding.provider == identity["provider"],
                ChunkEmbedding.model == identity["model"],
                ChunkEmbedding.dimensions == identity["dimensions"],
                ChunkEmbedding.provider_version == identity["provider_version"],
                ChunkEmbedding.embedding_set_version == identity["embedding_set_version"],
                ChunkEmbedding.embedding_schema_version == EMBEDDING_SCHEMA_VERSION,
                ChunkEmbedding.input_content_hash.in_(list(inputs.values())),
            )
            .order_by(ChunkEmbedding.created_at.desc())
        )
        hashes: dict[str, list[float]] = {}
        for input_hash, vector in rows:
            hashes.setdefault(input_hash, [float(value) for value in vector])
        return {key: hashes[h] for key, h in inputs.items() if h in hashes}

    async def save_vectors(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        rows: dict[str, tuple[dict[str, Any], list[float]]],
        ttl_days: int,
    ) -> None:
        if not rows:
            return
        expiry = datetime.now(UTC) + timedelta(days=ttl_days)
        stmt = insert(EmbeddingCache).values(
            [
                {
                    "id": uuid.uuid4(),
                    "project_id": project_id,
                    "cache_key": key,
                    "identity": identity,
                    "vector": [float(value) for value in vector],
                    "expires_at": expiry,
                }
                for key, (identity, vector) in rows.items()
            ]
        )
        await session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_embedding_cache_project_key",
                set_={
                    "vector": stmt.excluded.vector,
                    "identity": stmt.excluded.identity,
                    "expires_at": stmt.excluded.expires_at,
                    "updated_at": func.now(),
                },
            )
        )

    async def reserve(
        self, scope: ProviderWorkScope, endpoint: str, model: str, purpose: str, estimate: int
    ) -> uuid.UUID:
        async with self.sessions() as session, session.begin():
            # One deployment-wide admission lock includes in-flight and unknown-cost attempts.
            await session.execute(text("SELECT pg_advisory_xact_lock(728160203801)"))
            cfg = scope.config
            charge = func.coalesce(
                ProviderUsageAttempt.billed_micro_usd, ProviderUsageAttempt.reserved_micro_usd
            )
            start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            monthly = await session.scalar(
                select(func.coalesce(func.sum(charge), 0)).where(
                    ProviderUsageAttempt.created_at >= start
                )
            )
            operation = await session.scalar(
                select(func.coalesce(func.sum(charge), 0)).where(
                    ProviderUsageAttempt.project_id == scope.project_id,
                    ProviderUsageAttempt.reference == scope.reference,
                )
            )
            limit = (
                cfg.evaluation_budget_usd
                if scope.workload == "evaluation"
                else cfg.operation_budget_usd
            )
            if cfg.enforce_budgets and (
                int(monthly or 0) + estimate > micro_usd(Decimal(str(cfg.monthly_budget_usd)))
                or int(operation or 0) + estimate > micro_usd(Decimal(str(limit)))
            ):
                raise ProviderBudgetError(
                    "Configured provider spending budget exhausted", provider_name="cohere"
                )
            attempt = uuid.uuid4()
            session.add(
                ProviderUsageAttempt(
                    id=attempt,
                    project_id=scope.project_id,
                    reference=scope.reference,
                    workload=scope.workload,
                    environment=scope.environment,
                    endpoint=endpoint,
                    model=model,
                    purpose=purpose,
                    status="reserved",
                    price_version=cfg.price_version,
                    reserved_micro_usd=estimate,
                )
            )
            return attempt

    async def complete(
        self,
        attempt: uuid.UUID,
        status: str,
        tokens: int | None,
        units: int | None,
        cost: int | None,
    ) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(
                update(ProviderUsageAttempt)
                .where(ProviderUsageAttempt.id == attempt)
                .values(
                    status=status, billed_tokens=tokens, search_units=units, billed_micro_usd=cost
                )
            )


async def purge_project_cache(session: AsyncSession, project_id: uuid.UUID) -> None:
    """Passages lack document ownership: conservatively erase all project cache on purge."""
    await acquire_project_stage_lock(session, project_id=project_id, stage="embedding-cache")
    await session.execute(delete(EmbeddingCache).where(EmbeddingCache.project_id == project_id))
