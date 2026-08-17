"""Unit tests for SearchService strategy selection."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import AIConfigPolicy, RetrievalConfig, RetrievalStrategy
from app.core.exceptions import ServiceUnavailableError
from app.modules.retrieval.retrievers.models import CandidateHit, CandidateSource
from app.modules.retrieval.schemas.search import RetrievalResult, SearchRequest
from app.modules.retrieval.services.search_service import SearchService
from app.platform.config.project_ai import SourcePolicyMode

pytestmark = pytest.mark.unit


async def test_search_service_uses_request_strategy_override() -> None:
    project_id = uuid.uuid4()
    session = AsyncMock()
    embedder = MagicMock()
    reranker = MagicMock()
    config = RetrievalConfig(strategy=RetrievalStrategy.SEMANTIC)

    service = SearchService(
        session=session,
        project_id=project_id,
        embedder=embedder,
        reranker=reranker,
        retrieval_config=config,
    )

    retriever = MagicMock()
    retriever.retrieve = AsyncMock(
        return_value=[CandidateHit(uuid.uuid4(), 0.5, CandidateSource.SEMANTIC)]
    )
    service._build_retriever = MagicMock(return_value=retriever)  # type: ignore[method-assign]
    active_build = MagicMock(id=uuid.uuid4(), embedding_set_version=1)
    service._builds = MagicMock()
    service._builds.get_active = AsyncMock(return_value=active_build)
    service._hydrator = MagicMock()
    service._hydrator.hydrate = AsyncMock(
        return_value=[
            RetrievalResult(
                chunk_id=uuid.uuid4(),
                document_id=uuid.uuid4(),
                chunk_index=0,
                content="x",
                score=0.5,
                semantic_score=0.72,
                filename="f.txt",
            )
        ]
    )

    response = await service.search(SearchRequest(query="test", strategy=RetrievalStrategy.HYBRID))

    service._build_retriever.assert_called_once_with(RetrievalStrategy.HYBRID)
    assert response.top_k == config.default_top_k
    assert response.diagnostics.best_semantic_score == 0.72
    assert response.diagnostics.candidate_trace == response.diagnostics.selected_trace
    assert response.diagnostics.selected_trace[0]["semantic_score"] == 0.72
    assert "content" not in response.diagnostics.selected_trace[0]


async def test_source_policy_read_failure_fails_closed_only_in_enforce_mode() -> None:
    source_metadata = MagicMock()
    source_metadata.capture = AsyncMock(side_effect=SQLAlchemyError("unavailable"))
    enforce = SearchService(
        session=AsyncMock(),
        project_id=uuid.uuid4(),
        embedder=MagicMock(),
        reranker=MagicMock(),
        retrieval_config=RetrievalConfig(),
        source_metadata=source_metadata,
        configured_source_policy_mode=SourcePolicyMode.ENFORCE,
    )

    with pytest.raises(ServiceUnavailableError) as caught:
        await enforce._capture_source_scope(None)

    assert caught.value.code == "source_policy_unavailable"

    observe = SearchService(
        session=AsyncMock(),
        project_id=uuid.uuid4(),
        embedder=MagicMock(),
        reranker=MagicMock(),
        retrieval_config=RetrievalConfig(),
        ai_policy=AIConfigPolicy(source_policy_deployment_cap="observe"),
        source_metadata=source_metadata,
        configured_source_policy_mode=SourcePolicyMode.OBSERVE,
    )
    scope, status = await observe._capture_source_scope(None)

    assert scope.effective_mode is SourcePolicyMode.OFF
    assert status == "read_failed_fallback_off"


async def test_source_policy_off_skips_metadata_capture() -> None:
    source_metadata = MagicMock()
    source_metadata.capture = AsyncMock()
    service = SearchService(
        session=AsyncMock(),
        project_id=uuid.uuid4(),
        embedder=MagicMock(),
        reranker=MagicMock(),
        retrieval_config=RetrievalConfig(),
        source_metadata=source_metadata,
        configured_source_policy_mode=SourcePolicyMode.OFF,
    )

    scope, status = await service._capture_source_scope(None)

    assert scope.selectable is None
    assert status == "off"
    source_metadata.capture.assert_not_awaited()


async def test_enforce_overfetches_before_revision_consolidation_to_fill_top_k() -> None:
    project_id = uuid.uuid4()
    source_metadata = MagicMock()
    source_metadata.capture = AsyncMock(
        return_value=MagicMock(
            selectable=MagicMock(),
            generation=3,
            configured_mode=SourcePolicyMode.ENFORCE,
            effective_mode=SourcePolicyMode.ENFORCE,
            deployment_cap="enforce",
            reference_date="2026-08-16",
            explicit_as_of=None,
            exclusion_counts={},
        )
    )
    service = SearchService(
        session=AsyncMock(),
        project_id=project_id,
        embedder=MagicMock(),
        reranker=MagicMock(),
        retrieval_config=RetrievalConfig(),
        source_metadata=source_metadata,
        configured_source_policy_mode=SourcePolicyMode.ENFORCE,
    )
    active_build = MagicMock(id=uuid.uuid4(), embedding_set_version=1)
    service._builds = MagicMock()
    service._builds.get_active = AsyncMock(return_value=active_build)
    common_group = uuid.uuid4()
    candidates = [
        CandidateHit(
            uuid.uuid4(),
            1 - index / 100,
            CandidateSource.SEMANTIC,
            {
                "source_group_id": str(common_group),
                "source_revision_id": str(uuid.uuid4()),
            },
        )
        for index in range(30)
    ]
    candidates.extend(
        CandidateHit(
            uuid.uuid4(),
            0.6 - index / 100,
            CandidateSource.SEMANTIC,
            {
                "source_group_id": str(uuid.uuid4()),
                "source_revision_id": str(uuid.uuid4()),
            },
        )
        for index in range(10)
    )
    retriever = MagicMock()
    retriever.retrieve = AsyncMock(side_effect=lambda context: candidates[: context.top_k])
    service._build_retriever = MagicMock(return_value=retriever)  # type: ignore[method-assign]
    service._hydrator = MagicMock()
    service._hydrator.hydrate = AsyncMock(
        side_effect=lambda rows: [
            RetrievalResult(
                chunk_id=row.chunk_id,
                document_id=uuid.uuid4(),
                chunk_index=index,
                content=f"result-{index}",
                score=row.score,
                filename=f"result-{index}.txt",
            )
            for index, row in enumerate(rows)
        ]
    )

    response = await service.search(SearchRequest(query="policy", top_k=10))

    context = retriever.retrieve.await_args.args[0]
    assert context.top_k == 100
    assert len(response.results) == 10
