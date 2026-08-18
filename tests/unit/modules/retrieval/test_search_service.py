"""Unit tests for SearchService strategy selection."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import (
    AIConfigPolicy,
    QueryTranslationConfig,
    RetrievalConfig,
    RetrievalStrategy,
)
from app.core.exceptions import ServiceUnavailableError
from app.modules.retrieval.multilingual.planner import (
    BRANCH_ORIGINAL_DENSE,
    BRANCH_ORIGINAL_LEXICAL,
    BRANCH_TRANSLATED_DENSE,
    BRANCH_TRANSLATED_LEXICAL,
    LanguageInventory,
    MultilingualRetrievalPlan,
    RetrievalBranch,
)
from app.modules.retrieval.retrievers.models import CandidateHit, CandidateSource
from app.modules.retrieval.schemas.search import RetrievalResult, SearchRequest
from app.modules.retrieval.services.search_service import SearchService
from app.platform.config.project_ai import SourcePolicyMode
from app.platform.domain.language_detection import detect_query_language_profile

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


async def test_search_diagnostics_expose_translation_query_and_branch_provenance() -> None:
    project_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    document_id = uuid.uuid4()
    translated_query = "উৎসে কর সংগ্রহের খাত"
    profile = detect_query_language_profile("source tax deduction areas")
    plan = MultilingualRetrievalPlan(
        query_profile=profile,
        inventory=LanguageInventory(
            schema_version="2026-08-18.v1",
            chunk_language_counts={"bn": 8},
            document_language_counts={"bn": 1},
            is_legacy=False,
        ),
        translation_status="applied",
        target_language="bn",
        translated_query=translated_query,
        branches=(
            RetrievalBranch(
                branch_id=BRANCH_ORIGINAL_DENSE,
                family=BRANCH_ORIGINAL_DENSE,
                query="source tax deduction areas",
                language_scope=None,
            ),
            RetrievalBranch(
                branch_id=BRANCH_ORIGINAL_LEXICAL,
                family=BRANCH_ORIGINAL_LEXICAL,
                query="source tax deduction areas",
                language_scope=None,
            ),
            RetrievalBranch(
                branch_id=f"{BRANCH_TRANSLATED_DENSE}:bn",
                family=BRANCH_TRANSLATED_DENSE,
                query=translated_query,
                language_scope=None,
                target_language="bn",
            ),
            RetrievalBranch(
                branch_id=f"{BRANCH_TRANSLATED_LEXICAL}:bn",
                family=BRANCH_TRANSLATED_LEXICAL,
                query=translated_query,
                language_scope=None,
                target_language="bn",
            ),
        ),
        skipped_branches=(),
        diagnostics={
            "translation_status": "applied",
            "translation_source_language": profile.exact_primary or profile.profile,
            "translation_provider": "openai",
            "translation_model": "gpt-5-nano",
            "translation_target_language": "bn",
        },
    )
    service = SearchService(
        session=AsyncMock(),
        project_id=project_id,
        embedder=MagicMock(),
        reranker=MagicMock(),
        retrieval_config=RetrievalConfig(strategy=RetrievalStrategy.HYBRID),
        query_translation_config=QueryTranslationConfig(enabled=True),
        persist_translation_text=False,
    )
    retriever = MagicMock()
    retriever.retrieve = AsyncMock(
        return_value=[CandidateHit(chunk_id, 0.032, CandidateSource.HYBRID)]
    )
    service._build_retriever = MagicMock(return_value=retriever)  # type: ignore[method-assign]
    service._builds = MagicMock()
    service._builds.get_active = AsyncMock(
        return_value=MagicMock(id=uuid.uuid4(), embedding_set_version=2, manifest={})
    )
    service._hydrator = MagicMock()
    service._hydrator.hydrate = AsyncMock(
        return_value=[
            RetrievalResult(
                chunk_id=chunk_id,
                document_id=document_id,
                chunk_index=2,
                content="sourse tax table",
                score=0.032,
                semantic_score=0.18,
                filename="gazette.pdf",
                metadata={
                    "retrieval_source": "hybrid",
                    "reranker_score_scale": "reciprocal_rank_fusion",
                    "translation_status": "applied",
                    "rrf_contributions": [
                        {
                            "branch_id": "original_dense",
                            "family": "original_dense",
                            "rank": 8,
                            "raw_score": 0.18,
                            "rrf": 0.0147,
                        },
                        {
                            "branch_id": "translated_dense:bn",
                            "family": "translated_dense",
                            "target_language": "bn",
                            "rank": 1,
                            "raw_score": 0.71,
                            "rrf": 0.0164,
                        },
                        {
                            "branch_id": "translated_lexical:bn",
                            "family": "translated_lexical",
                            "target_language": "bn",
                            "rank": 1,
                            "raw_score": 12.4,
                            "rrf": 0.0164,
                        },
                    ],
                },
            )
        ]
    )

    with patch(
        "app.modules.retrieval.services.search_service.resolve_multilingual_plan",
        AsyncMock(return_value=plan),
    ):
        response = await service.search(SearchRequest(query="source tax deduction areas"))

    assert response.diagnostics.translation_status == "applied"
    assert response.diagnostics.translation_target_language == "bn"
    assert response.diagnostics.translation_model == "gpt-5-nano"
    assert response.diagnostics.translated_query == translated_query
    assert "translated_query" not in response.results[0].metadata
    trace = response.diagnostics.selected_trace[0]
    assert "content" not in trace
    assert trace["original_dense"]["rank"] == 8
    assert trace["translated_dense"]["rank"] == 1
    assert trace["translated_dense"]["score"] == 0.71
    assert trace["translated_lexical"]["rank"] == 1
    assert trace["rrf_score"] == pytest.approx(0.0475)
    assert f"{BRANCH_TRANSLATED_DENSE}:bn" in trace["branch_provenance"]


async def test_search_returns_mismatch_diagnostic_when_live_embedder_differs() -> None:
    embedder = MagicMock()
    embedder.provider_name = "cohere"
    embedder.model_name = "embed-v4.0"
    service = SearchService(
        session=AsyncMock(),
        project_id=uuid.uuid4(),
        embedder=embedder,
        reranker=MagicMock(),
        retrieval_config=RetrievalConfig(),
    )
    service._builds = MagicMock()
    service._builds.get_active = AsyncMock(
        return_value=MagicMock(
            id=uuid.uuid4(),
            embedding_set_version=2,
            manifest={
                "embedding_provider": "openai",
                "embedding_model": "text-embedding-3-large",
            },
        )
    )
    retriever = MagicMock()
    retriever.retrieve = AsyncMock()
    service._build_retriever = MagicMock(return_value=retriever)  # type: ignore[method-assign]

    response = await service.search(SearchRequest(query="policy"))

    assert response.results == []
    assert response.diagnostics.rerank_status == "embedding_identity_mismatch"
    assert response.diagnostics.embedding_identity_status == "mismatch"
    assert response.diagnostics.embedding_provider == "openai"
    assert response.diagnostics.embedding_model == "text-embedding-3-large"
    retriever.retrieve.assert_not_called()

