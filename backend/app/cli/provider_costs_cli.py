"""Read-only provider billing report and uncached corpus cost preview."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk
from app.models.provider_work import ProviderUsageAttempt
from app.platform.db.session import Database
from app.platform.infra.providers.provider_work_repository import ProviderWorkRepository
from app.platform.providers.contracts.embedding import EmbeddingPurpose
from app.platform.providers.implementations.embedding_factory import create_embedding_provider
from app.platform.providers.provider_work import cache_identity, cache_key, micro_usd


async def report(project_id: uuid.UUID, month: str, preview: bool) -> dict[str, Any]:
    settings = get_settings()
    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    database = Database(settings)
    store = ProviderWorkRepository(database.session_factory)
    try:
        async with database.session_factory() as session:
            amount = func.coalesce(
                ProviderUsageAttempt.billed_micro_usd, ProviderUsageAttempt.reserved_micro_usd
            )
            groups = await session.execute(
                select(
                    ProviderUsageAttempt.workload,
                    ProviderUsageAttempt.endpoint,
                    ProviderUsageAttempt.purpose,
                    func.count().label("attempts"),
                    func.sum(ProviderUsageAttempt.billed_tokens).label("billed_tokens"),
                    func.sum(ProviderUsageAttempt.search_units).label("search_units"),
                    func.sum(amount).label("accounted_micro_usd"),
                    func.count()
                    .filter(ProviderUsageAttempt.billed_micro_usd.is_(None))
                    .label("unknown_cost_attempts"),
                )
                .where(
                    ProviderUsageAttempt.project_id == project_id,
                    ProviderUsageAttempt.created_at >= start,
                    ProviderUsageAttempt.created_at < end,
                )
                .group_by(
                    ProviderUsageAttempt.workload,
                    ProviderUsageAttempt.endpoint,
                    ProviderUsageAttempt.purpose,
                )
            )
            result: dict[str, Any] = {
                "project_id": str(project_id),
                "month_utc": month,
                "usage": [dict(row._mapping) for row in groups],
                "note": (
                    "Unknown costs retain reservations; historical unrecorded calls are absent."
                ),
            }
            if preview:
                chunks = list(
                    (
                        await session.scalars(
                            select(DocumentChunk.content)
                            .join(Document, Document.id == DocumentChunk.document_id)
                            .where(
                                Document.project_id == project_id,
                                DocumentChunk.project_id == project_id,
                                Document.deleted_at.is_(None),
                                Document.status.in_(
                                    [
                                        DocumentStatus.CHUNKED,
                                        DocumentStatus.EMBEDDED,
                                        DocumentStatus.READY,
                                        DocumentStatus.EMBEDDING,
                                        DocumentStatus.INDEXING,
                                    ]
                                ),
                                DocumentChunk.document_version == Document.version,
                                DocumentChunk.generation_id.is_not_distinct_from(
                                    Document.chunk_generation_id
                                ),
                            )
                        )
                    ).all()
                )
                provider = create_embedding_provider(settings)
                identity = cache_identity(
                    provider, settings.retrieval.embedding_set_version, EmbeddingPurpose.DOCUMENT
                )
                inputs = {cache_key(identity, text): text for text in chunks}
                existing = (
                    await store.vectors(session, project_id, list(inputs))
                    if settings.provider_costs.cache_enabled
                    else {}
                )
                if settings.provider_costs.cache_enabled:
                    missing = {
                        key: hashlib.sha256(text.encode()).hexdigest()
                        for key, text in inputs.items()
                        if key not in existing
                    }
                    existing.update(
                        await store.seed_vectors(session, project_id, identity, missing)
                    )
                uncached = (
                    [text for key, text in inputs.items() if key not in existing]
                    if settings.provider_costs.cache_enabled
                    else chunks
                )
                estimate = micro_usd(
                    Decimal(sum(len(t.encode()) for t in uncached))
                    * Decimal(str(settings.provider_costs.embedding_usd_per_million))
                    / 1_000_000
                )
                if provider.provider_name != "cohere" or provider.model_name != "embed-v4.0":
                    estimate = 0
                result["build_preview"] = {
                    "chunks": len(chunks),
                    "unique_inputs": len(inputs),
                    "uncached_inputs": len(uncached),
                    "estimated_micro_usd": estimate,
                    "estimate_basis": (
                        "UTF-8 bytes at configured Cohere v4 rate; zero for other models; "
                        "corpus can change"
                    ),
                }
            return result
    finally:
        await database.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli provider-costs")
    parser.add_argument("--project", type=uuid.UUID, required=True)
    parser.add_argument("--month", default=datetime.now(UTC).strftime("%Y-%m"))
    parser.add_argument("--preview-build", action="store_true")
    args = parser.parse_args(argv)
    payload = json.dumps(
        asyncio.run(report(args.project, args.month, args.preview_build)), indent=2
    )
    sys.stdout.write(f"{payload}\n")
    return 0
