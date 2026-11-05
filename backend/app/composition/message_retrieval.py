"""Shared Message retrieval, snapshot validation, and bounded recovery composition."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.composition.source_metadata import KnowledgeRetrievalSourceMetadataAdapter
from app.models.index_scope_review import IndexScopeReview
from app.modules.conversations.ports import ContextChunk, ContextRetrievalResult
from app.modules.retrieval.known_dependency_gap import known_dependency_gap
from app.modules.retrieval.repositories.retrieval_chunk_repository import RetrievalChunkRepository
from app.modules.retrieval.schemas.search import SearchRequest
from app.modules.retrieval.services.search_service import SearchService
from app.platform.db.session import ObservedAsyncSession
from app.platform.infra.recovery_capacity import recovery_slot
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider, EmbeddingPurpose
from app.platform.providers.errors import ProviderError, ProviderTimeoutError
from app.platform.providers.request_work import (
    CachedEmbeddingProvider,
    RequestWork,
    current_request_work,
)


class SearchServiceRetrievalAdapter:
    """Maps retrieval SearchService to the conversations RetrievalPort."""

    supports_adjacent_retrieval = True
    supports_cited_retrieval = True
    supports_batch_retrieval = True
    supports_exact_recall = True
    supports_preceding_scope = True

    def __init__(
        self,
        search_service: SearchService,
        *,
        branch_factory: Callable[[AsyncSession, dict[str, Any]], SearchService] | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        redis_dsn: str | None = None,
    ) -> None:
        self._search_service = search_service
        self._branch_factory = branch_factory
        self._session_factory = session_factory
        self._redis_dsn = redis_dsn

    def _request_work(self) -> RequestWork | None:
        embedder = getattr(self._search_service, "resolved_query_embedder", None)
        work = getattr(embedder, "work", None)
        if isinstance(work, RequestWork):
            return work
        return current_request_work()

    def set_request_scope(self, scope: dict[str, Any]) -> None:
        self._search_service.set_request_scope(scope)

    async def pin_validated_preview(self, build_id: uuid.UUID) -> None:
        await self._search_service.pin_validated_preview(build_id)

    async def preceding_indexed_text(
        self, chunk_ids: list[uuid.UUID], *, index_build_id: str
    ) -> dict[uuid.UUID, str]:
        return await RetrievalChunkRepository(
            self._search_service._session, self._search_service._project_id
        ).preceding_indexed_text(chunk_ids, index_build_id=uuid.UUID(index_build_id))

    async def retrieve_batch(
        self, requests: list[dict[str, Any]], *, snapshot: dict[str, Any]
    ) -> list[ContextRetrievalResult]:
        work = self._request_work()
        if work is not None:
            work.counts["recovery_attempts"] += 1
        if self._branch_factory is None or self._session_factory is None:
            completed: list[ContextRetrievalResult] = []
            for request in requests:
                try:
                    completed.append(await self.retrieve(**request))
                except (ProviderTimeoutError, TimeoutError):
                    completed.append(
                        ContextRetrievalResult(
                            chunks=[],
                            diagnostics={
                                **snapshot,
                                "branch_failure": "provider_or_deadline_failure",
                            },
                        )
                    )
            return completed
        # The initial search resolved the active build's embedding identity. Warm
        # only that turn-local cache; branches still resolve and verify their own
        # pinned snapshots and execute all retrieval/admission checks as before.
        embedder = getattr(self._search_service, "resolved_query_embedder", None)
        if isinstance(embedder, CachedEmbeddingProvider) and snapshot.get("strategy") in {
            "hybrid",
            "semantic",
        }:
            queries = list(dict.fromkeys(request["query"] for request in requests))
            if queries:
                await embedder.embed_texts(queries, purpose=EmbeddingPurpose.QUERY)
                embedder.work.counts["recovery_query_embedding_batches"] += 1
        branch_factory, session_factory = self._branch_factory, self._session_factory
        # Eight bounded overview searches complete in two waves. The shared
        # process and deployment limits still constrain aggregate concurrency.
        limiter = asyncio.Semaphore(4)

        async def branch(request: dict[str, Any]) -> ContextRetrievalResult:
            async with (
                _hold_semaphore(work, limiter, "recovery_batch_semaphore"),
                _hold_semaphore(work, _RECOVERY_LIMIT, "process_recovery_semaphore"),
                self._deployment_slot(work),
                session_factory() as session,
            ):
                if isinstance(session, ObservedAsyncSession):
                    await session.connection()
                adapter = SearchServiceRetrievalAdapter(branch_factory(session, snapshot))
                result = await adapter.retrieve(**request)
                for key in (
                    "index_build_id",
                    "source_metadata_generation",
                    "configuration_hash",
                    "reference_date",
                ):
                    if (
                        snapshot.get(key) is not None
                        and result.diagnostics.get(key) != snapshot[key]
                    ):
                        raise ProviderError(
                            "Recovery snapshot changed",
                            provider_name="retrieval",
                            context={"reason": f"snapshot_mismatch_{key}"},
                        )
                return result

        tasks = [asyncio.create_task(branch(request)) for request in requests]
        deadline = snapshot.get("discovery_deadline")
        if not isinstance(deadline, (float, int)):
            deadline = work.recovery_deadline if work is not None else time.perf_counter() + 30
        try:
            pending = set(tasks)
            fatal = None
            while pending:
                done, pending = await asyncio.wait(
                    pending,
                    timeout=max(0.0, deadline - time.perf_counter()),
                    return_when=asyncio.FIRST_EXCEPTION,
                )
                fatal = next(
                    (
                        task.exception()
                        for task in done
                        if not task.cancelled()
                        and task.exception() is not None
                        and not isinstance(task.exception(), (ProviderTimeoutError, TimeoutError))
                    ),
                    None,
                )
                if fatal is not None or not done or time.perf_counter() >= deadline:
                    break
                # A recoverable independent timeout does not consume the
                # remaining sibling's discovery window.
            for task in pending:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            if fatal is not None:
                # Snapshot identity and provider integrity errors never become empty
                # successful branches. Recovery orchestrates safe partial checkpoints.
                raise fatal
            return [
                result
                if isinstance(result, ContextRetrievalResult)
                else ContextRetrievalResult(
                    [], {**snapshot, "branch_failure": "discovery_deadline_exceeded"}
                )
                for result in results
            ]
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    @asynccontextmanager
    async def _deployment_slot(self, work: RequestWork | None = None) -> AsyncIterator[None]:
        del work
        if self._redis_dsn is None:
            yield
            return
        async with recovery_slot(self._redis_dsn):
            yield

    @property
    def query_embedder(self) -> BaseEmbeddingProvider | None:
        return self._search_service.resolved_query_embedder

    async def retrieve(
        self,
        *,
        query: str,
        top_k: int,
        document_id: uuid.UUID | None = None,
        metadata_filter: dict[str, str] | None = None,
        as_of: datetime | None = None,
        adjacent_to: list[uuid.UUID] | None = None,
        cited_chunk_ids: list[uuid.UUID] | None = None,
    ) -> ContextRetrievalResult:
        work = self._request_work()
        if work is not None:
            work.counts["ranked_retrieval_calls"] += 1
        response = await self._search_service.search(
            SearchRequest(
                query=query,
                top_k=top_k,
                document_id=document_id,
                metadata_filter=metadata_filter or {},
                as_of=as_of,
            ),
            adjacent_to=adjacent_to,
            cited_chunk_ids=cited_chunk_ids,
        )
        diagnostics = response.diagnostics.model_dump(mode="json")
        await self._freeze_scope_fingerprint(diagnostics)
        diagnostics.update(
            known_dependency_gap(diagnostics, getattr(self._search_service, "_project_id", None))
        )
        return ContextRetrievalResult(
            chunks=[ContextChunk.from_retrieval_result(result) for result in response.results],
            diagnostics=diagnostics,
        )

    async def _freeze_scope_fingerprint(self, diagnostics: dict[str, Any]) -> None:
        work = self._request_work()
        if (
            work is not None
            and isinstance(self._search_service, SearchService)
            and diagnostics.get("index_build_id")
        ):
            frozen = work.evidence_snapshot
            if "scope_review_fingerprint" not in frozen:
                rows = (
                    await self._search_service._session.execute(
                        select(IndexScopeReview.chunk_id, IndexScopeReview.review_hash)
                        .where(
                            IndexScopeReview.project_id == self._search_service._project_id,
                            IndexScopeReview.build_id
                            == uuid.UUID(str(diagnostics["index_build_id"])),
                        )
                        .order_by(IndexScopeReview.chunk_id)
                    )
                ).all()
                frozen["scope_review_fingerprint"] = hashlib.sha256(
                    json.dumps(
                        [
                            {"chunk_id": str(row.chunk_id), "review_hash": row.review_hash}
                            for row in rows
                        ],
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode()
                ).hexdigest()
            diagnostics["scope_review_fingerprint"] = frozen["scope_review_fingerprint"]

    async def retrieve_exact(
        self,
        *,
        chunk_ids: list[uuid.UUID],
        query: str = "",
        document_id: uuid.UUID | None = None,
        metadata_filter: dict[str, str] | None = None,
        as_of: datetime | None = None,
    ) -> ContextRetrievalResult:
        identities = list(dict.fromkeys(chunk_ids))
        if not identities:
            return ContextRetrievalResult(
                chunks=[],
                diagnostics={
                    "identity_recall_status": "empty_restriction",
                    "skipped_reason": "empty_identity_restriction",
                    "rerank_status": "skipped",
                    "retrieved_candidate_count": 0,
                },
            )
        response = await self._search_service.recall_indexed_identities(
            chunk_ids=identities,
            query=query,
            document_id=document_id,
            metadata_filter=metadata_filter,
            as_of=as_of,
        )
        diagnostics = response.diagnostics.model_dump(mode="json")
        await self._freeze_scope_fingerprint(diagnostics)
        return ContextRetrievalResult(
            chunks=[ContextChunk.from_retrieval_result(result) for result in response.results],
            diagnostics=diagnostics,
        )


# Worker-process wide bound in addition to the per-turn concurrency of three.
_RECOVERY_LIMIT = asyncio.Semaphore(12)


@asynccontextmanager
async def _hold_semaphore(
    work: RequestWork | None, semaphore: asyncio.Semaphore, name: str
) -> AsyncIterator[None]:
    """Acquire a semaphore, ending the wait span before the protected operation."""

    if work is None:
        async with semaphore:
            yield
        return
    async with work.wait(name):
        await semaphore.acquire()
    try:
        yield
    finally:
        semaphore.release()


def clone_search_for_snapshot(
    prototype: SearchService, session: AsyncSession, snapshot: dict[str, Any]
) -> SearchService:
    """Reuse the exact profile/provider policy and pin every recovery branch."""
    return SearchService(
        session=session,
        project_id=prototype._project_id,
        embedder=prototype.resolved_query_embedder or prototype._embedder,
        reranker=prototype._reranker,
        retrieval_config=prototype._config,
        ai_policy=prototype._ai_policy,
        source_metadata=KnowledgeRetrievalSourceMetadataAdapter(session),
        configured_source_policy_mode=prototype._configured_source_policy_mode,
        configuration_hash=prototype._configuration_hash,
        config_provenance=prototype._config_provenance,
        query_translator=prototype._query_translator,
        query_translation_config=prototype._query_translation_config,
        query_embedder_factory=prototype._query_embedder_factory,
        persist_translation_text=prototype._persist_translation_text,
        pinned_index_build_id=uuid.UUID(str(snapshot["index_build_id"])),
        pinned_source_metadata_generation=snapshot["source_metadata_generation"],
        pinned_reference_date=snapshot.get("reference_date"),
        normalized_scope=snapshot.get("normalized_scope"),
        work=current_request_work(),
    )
