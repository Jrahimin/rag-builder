"""Unit tests for SearchService strategy selection."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import (
    AIConfigPolicy,
    ModifiesExpansionMode,
    QueryTranslationConfig,
    RerankMode,
    RetrievalConfig,
    RetrievalStrategy,
)
from app.core.exceptions import ConflictError, ServiceUnavailableError
from app.modules.retrieval.embedding_identity import EmbeddingIdentity
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
from app.platform.domain.evidence_contracts import QueryVariant, QueryVariantKind
from app.platform.domain.language_detection import detect_query_language_profile
from app.platform.providers.errors import ProviderError

pytestmark = pytest.mark.unit


def _embedder(
    *,
    provider: str = "hash",
    model: str = "text-embedding-3-large",
    dimensions: int = 1024,
) -> MagicMock:
    embedder = MagicMock()
    embedder.provider_name = provider
    embedder.model_name = model
    embedder.dimensions = dimensions
    return embedder


def _labeled_build(
    *,
    embedding_set_version: int = 2,
    provider: str = "hash",
    model: str = "text-embedding-3-large",
    dimensions: int = 1024,
    manifest: dict | None = None,
) -> MagicMock:
    return MagicMock(
        id=uuid.uuid4(),
        embedding_set_version=embedding_set_version,
        manifest=manifest
        if manifest is not None
        else {
            "embedding_provider": provider,
            "embedding_model": model,
            "embedding_dimensions": dimensions,
            "embedding_set_version": embedding_set_version,
        },
    )


def _search_service(
    *,
    embedder: MagicMock | None = None,
    retrieval_config: RetrievalConfig | None = None,
    query_embedder_factory=None,
    **kwargs,
) -> SearchService:
    return SearchService(
        session=AsyncMock(),
        project_id=uuid.uuid4(),
        embedder=embedder or _embedder(),
        reranker=kwargs.pop("reranker", MagicMock()),
        retrieval_config=retrieval_config or RetrievalConfig(),
        query_embedder_factory=query_embedder_factory,
        **kwargs,
    )


def _ready_search(service: SearchService, *, build: MagicMock | None = None) -> MagicMock:
    active_build = build or _labeled_build()
    service._builds = MagicMock()
    service._builds.get_active = AsyncMock(return_value=active_build)
    retriever = MagicMock()
    retriever.retrieve = AsyncMock(
        return_value=[CandidateHit(uuid.uuid4(), 0.5, CandidateSource.SEMANTIC)]
    )
    service._build_retriever = MagicMock(return_value=retriever)  # type: ignore[method-assign]
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
    return retriever


async def test_search_service_uses_request_strategy_override() -> None:
    config = RetrievalConfig(strategy=RetrievalStrategy.SEMANTIC)
    service = _search_service(retrieval_config=config)
    _ready_search(service)

    response = await service.search(SearchRequest(query="test", strategy=RetrievalStrategy.HYBRID))

    assert service._build_retriever.call_args.args[0] is RetrievalStrategy.HYBRID
    assert response.top_k == config.default_top_k
    assert response.diagnostics.best_semantic_score == 0.72
    assert response.diagnostics.candidate_trace == response.diagnostics.selected_trace
    assert response.diagnostics.selected_trace[0]["semantic_score"] == 0.72
    assert "content" not in response.diagnostics.selected_trace[0]
    assert response.diagnostics.embedding_identity_status == "matched"


async def test_source_policy_read_failure_fails_closed_only_in_enforce_mode() -> None:
    source_metadata = MagicMock()
    source_metadata.capture = AsyncMock(side_effect=SQLAlchemyError("unavailable"))
    enforce = SearchService(
        session=AsyncMock(),
        project_id=uuid.uuid4(),
        embedder=_embedder(),
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
        embedder=_embedder(),
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
        embedder=_embedder(),
        reranker=MagicMock(),
        retrieval_config=RetrievalConfig(),
        source_metadata=source_metadata,
        configured_source_policy_mode=SourcePolicyMode.OFF,
    )

    scope, status = await service._capture_source_scope(None)

    assert scope.selectable is None
    assert status == "off"
    source_metadata.capture.assert_not_awaited()


async def test_modifies_expansion_captures_governance_even_when_source_policy_is_off() -> None:
    source_metadata = MagicMock()
    source_metadata.capture = AsyncMock(
        return_value=MagicMock(
            selectable=MagicMock(),
            generation=4,
            configured_mode=SourcePolicyMode.OFF,
            effective_mode=SourcePolicyMode.OFF,
            deployment_cap="enforce",
            reference_date="2026-08-31",
            explicit_as_of=None,
            exclusion_counts={},
        )
    )
    service = _search_service(
        retrieval_config=RetrievalConfig(modifies_expansion_enabled=True),
        source_metadata=source_metadata,
        configured_source_policy_mode=SourcePolicyMode.OFF,
    )

    scope, status = await service._capture_source_scope(None)

    assert scope.generation == 4
    assert status == "off"
    source_metadata.capture.assert_awaited_once()


async def test_observe_modifies_expansion_captures_governance_when_source_policy_is_off() -> None:
    source_metadata = MagicMock()
    source_metadata.capture = AsyncMock(
        return_value=MagicMock(
            selectable=MagicMock(),
            generation=4,
            configured_mode=SourcePolicyMode.OFF,
            effective_mode=SourcePolicyMode.OFF,
            deployment_cap="enforce",
            reference_date="2026-08-31",
            explicit_as_of=None,
            exclusion_counts={},
        )
    )
    service = _search_service(
        retrieval_config=RetrievalConfig(modifies_expansion_mode=ModifiesExpansionMode.OBSERVE),
        source_metadata=source_metadata,
        configured_source_policy_mode=SourcePolicyMode.OFF,
    )

    scope, status = await service._capture_source_scope(None)

    assert scope.generation == 4
    assert status == "off"
    source_metadata.capture.assert_awaited_once()


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
        embedder=_embedder(),
        reranker=MagicMock(),
        retrieval_config=RetrievalConfig(),
        source_metadata=source_metadata,
        configured_source_policy_mode=SourcePolicyMode.ENFORCE,
    )
    retriever = MagicMock()
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
    retriever.retrieve = AsyncMock(side_effect=lambda context: candidates[: context.top_k])
    service._builds = MagicMock()
    service._builds.get_active = AsyncMock(return_value=_labeled_build())
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
    assert response.diagnostics.reranked_candidate_count == 40
    assert response.diagnostics.post_rerank_removed_count == 30
    assert response.diagnostics.post_rerank_removal_reasons == {
        "same_source_group_lower_ranked_revision": 29,
        "result_limit": 1,
    }
    assert response.diagnostics.post_rerank_unfilled_slots == 0


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
        embedder=_embedder(),
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
    service._builds.get_active = AsyncMock(return_value=_labeled_build())
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


async def test_search_forwards_document_scope_to_multilingual_plan() -> None:
    document_id = uuid.uuid4()
    profile = detect_query_language_profile("উৎসে কর")
    plan = MultilingualRetrievalPlan(
        query_profile=profile,
        inventory=LanguageInventory(
            schema_version="2026-08-18.v1",
            chunk_language_counts={"bn": 8},
            document_language_counts={"bn": 1},
            is_legacy=False,
        ),
        translation_status="skipped",
        target_language=None,
        translated_query=None,
        branches=(
            RetrievalBranch(
                branch_id=BRANCH_ORIGINAL_DENSE,
                family=BRANCH_ORIGINAL_DENSE,
                query="উৎসে কর",
                language_scope=None,
            ),
            RetrievalBranch(
                branch_id=BRANCH_ORIGINAL_LEXICAL,
                family=BRANCH_ORIGINAL_LEXICAL,
                query="উৎসে কর",
                language_scope=None,
            ),
        ),
        skipped_branches=(BRANCH_TRANSLATED_DENSE, BRANCH_TRANSLATED_LEXICAL),
    )
    service = _search_service(
        retrieval_config=RetrievalConfig(strategy=RetrievalStrategy.HYBRID),
        query_translation_config=QueryTranslationConfig(enabled=True),
    )
    _ready_search(service)
    resolve = AsyncMock(return_value=plan)
    with patch(
        "app.modules.retrieval.services.search_service.resolve_multilingual_plan",
        resolve,
    ):
        await service.search(SearchRequest(query="উৎসে কর", document_id=document_id))
    assert resolve.await_args.kwargs["document_id"] == document_id


async def test_public_search_redacts_translated_query_variant_text() -> None:
    translated_query = "উৎস কর কর্তনের ক্ষেত্রগুলো কী কী"
    original = QueryVariant(
        variant_id="original",
        kind=QueryVariantKind.ORIGINAL,
        language="en",
        text="source tax deduction areas",
    )
    translated = QueryVariant(
        variant_id="translated:bn",
        kind=QueryVariantKind.TRANSLATED,
        language="bn",
        text=translated_query,
        source_variant_id="original",
    )
    service = _search_service(
        retrieval_config=RetrievalConfig(strategy=RetrievalStrategy.HYBRID),
        persist_translation_text=False,
    )
    _ready_search(service)
    service._hydrator.hydrate = AsyncMock(
        return_value=[
            RetrievalResult(
                chunk_id=uuid.uuid4(),
                document_id=uuid.uuid4(),
                chunk_index=0,
                content="x",
                score=0.5,
                filename="f.txt",
                query_variants=(original, translated),
            )
        ]
    )
    profile = detect_query_language_profile("source tax deduction areas")
    plan = MultilingualRetrievalPlan(
        query_profile=profile,
        inventory=LanguageInventory(
            schema_version="2026-08-18.v1",
            chunk_language_counts={"bn": 3},
            document_language_counts={"bn": 1},
            is_legacy=False,
        ),
        translation_status="applied",
        target_language="bn",
        translated_query=translated_query,
        branches=(),
        skipped_branches=(),
        diagnostics={"translation_status": "applied"},
        query_variants=(original, translated),
    )

    with patch(
        "app.modules.retrieval.services.search_service.resolve_multilingual_plan",
        AsyncMock(return_value=plan),
    ):
        public = await service.search(
            SearchRequest(query="source tax deduction areas"),
            for_public_response=True,
        )
        internal = await service.search(SearchRequest(query="source tax deduction areas"))

    public_translated = next(
        item
        for item in public.results[0].query_variants
        if item.kind is QueryVariantKind.TRANSLATED
    )
    internal_translated = next(
        item
        for item in internal.results[0].query_variants
        if item.kind is QueryVariantKind.TRANSLATED
    )
    assert public_translated.variant_id == "translated:bn"
    assert public_translated.text == ""
    assert public.diagnostics.translated_query is None
    assert internal_translated.text == translated_query
    assert internal.diagnostics.translated_query == translated_query


async def test_search_uses_active_openai_identity_while_target_settings_are_cohere() -> None:
    live = _embedder(provider="cohere", model="embed-v4.0")
    openai = _embedder(provider="openai", model="text-embedding-3-large")
    seen: list[EmbeddingIdentity] = []

    def factory(identity: EmbeddingIdentity):
        seen.append(identity)
        return openai

    service = _search_service(embedder=live, query_embedder_factory=factory)
    build = _labeled_build(provider="openai", model="text-embedding-3-large")
    retriever = _ready_search(service, build=build)

    response = await service.search(SearchRequest(query="policy"))

    assert seen[0].provider == "openai"
    assert service._build_retriever.call_args.args[1] is openai
    assert response.diagnostics.embedding_provider == "openai"
    assert response.diagnostics.embedding_model == "text-embedding-3-large"
    assert response.diagnostics.embedding_dimensions == 1024
    assert response.diagnostics.embedding_set_version == 2
    assert response.results
    retriever.retrieve.assert_awaited()


async def test_search_after_activation_uses_cohere_identity() -> None:
    live = _embedder(provider="cohere", model="embed-v4.0")
    cohere = _embedder(provider="cohere", model="embed-v4.0")

    def factory(identity: EmbeddingIdentity):
        assert identity.provider == "cohere"
        return cohere

    service = _search_service(embedder=live, query_embedder_factory=factory)
    _ready_search(
        service,
        build=_labeled_build(
            embedding_set_version=3,
            provider="cohere",
            model="embed-v4.0",
        ),
    )

    response = await service.search(SearchRequest(query="policy"))

    assert response.diagnostics.embedding_provider == "cohere"
    assert response.diagnostics.embedding_set_version == 3
    assert service._build_retriever.call_args.args[1] is cohere


async def test_search_rollback_uses_retained_openai_identity() -> None:
    live = _embedder(provider="cohere", model="embed-v4.0")
    openai = _embedder(provider="openai", model="text-embedding-3-large")

    def factory(identity: EmbeddingIdentity):
        return openai if identity.provider == "openai" else live

    service = _search_service(embedder=live, query_embedder_factory=factory)
    _ready_search(
        service,
        build=_labeled_build(provider="openai", model="text-embedding-3-large"),
    )

    response = await service.search(SearchRequest(query="policy"))

    assert response.diagnostics.embedding_provider == "openai"
    assert service._build_retriever.call_args.args[1] is openai


async def test_search_recovers_unlabeled_retained_build_from_stored_vectors() -> None:
    live = _embedder(provider="cohere", model="embed-v4.0")
    openai = _embedder(provider="openai", model="text-embedding-3-large")

    def factory(identity: EmbeddingIdentity):
        assert identity.source == "vectors"
        return openai

    service = _search_service(embedder=live, query_embedder_factory=factory)
    unlabeled = MagicMock(id=uuid.uuid4(), embedding_set_version=2, manifest={})
    service._embeddings = MagicMock()
    service._embeddings.list_distinct_identities = AsyncMock(
        return_value=[(2, "openai", "text-embedding-3-large", 1024)]
    )
    _ready_search(service, build=unlabeled)

    response = await service.search(SearchRequest(query="policy"))

    assert response.diagnostics.embedding_provider == "openai"
    assert response.diagnostics.embedding_identity_status == "matched"
    assert service._build_retriever.call_args.args[1] is openai
    service = _search_service()
    service._builds = MagicMock()
    service._builds.get_active = AsyncMock(
        return_value=MagicMock(id=uuid.uuid4(), embedding_set_version=2, manifest={})
    )
    service._embeddings = MagicMock()
    service._embeddings.list_distinct_identities = AsyncMock(return_value=[])
    retriever = MagicMock()
    retriever.retrieve = AsyncMock()
    service._build_retriever = MagicMock(return_value=retriever)  # type: ignore[method-assign]

    with pytest.raises(ConflictError) as caught:
        await service.search(SearchRequest(query="policy"))

    assert caught.value.code == "embedding_identity_unlabeled"
    retriever.retrieve.assert_not_called()


async def test_search_raises_when_injected_embedder_does_not_match_identity() -> None:
    service = _search_service(embedder=_embedder(provider="cohere", model="embed-v4.0"))
    service._builds = MagicMock()
    service._builds.get_active = AsyncMock(
        return_value=_labeled_build(provider="openai", model="text-embedding-3-large")
    )
    retriever = MagicMock()
    retriever.retrieve = AsyncMock()
    service._build_retriever = MagicMock(return_value=retriever)  # type: ignore[method-assign]

    with pytest.raises(ConflictError) as caught:
        await service.search(SearchRequest(query="policy"))

    assert caught.value.code == "embedding_identity_incompatible"
    retriever.retrieve.assert_not_called()


async def test_search_raises_when_active_identity_credentials_are_missing() -> None:
    def factory(identity: EmbeddingIdentity):
        del identity
        raise ProviderError("Cohere embedding backend requires APE_COHERE__API_KEY")

    service = _search_service(
        embedder=_embedder(provider="cohere", model="embed-v4.0"),
        query_embedder_factory=factory,
    )
    service._builds = MagicMock()
    service._builds.get_active = AsyncMock(
        return_value=_labeled_build(embedding_set_version=3, provider="cohere", model="embed-v4.0")
    )

    with pytest.raises(ServiceUnavailableError) as caught:
        await service.search(SearchRequest(query="policy"))

    assert caught.value.code == "embedding_provider_unavailable"


async def test_search_respects_project_rerank_mode_off() -> None:
    service = _search_service(
        retrieval_config=RetrievalConfig(
            strategy=RetrievalStrategy.HYBRID, rerank_mode=RerankMode.OFF
        )
    )
    retriever = _ready_search(service)

    await service.search(SearchRequest(query="policy"))

    context = retriever.retrieve.await_args.args[0]
    assert context.rerank_mode is RerankMode.OFF
    assert context.rerank_enabled is True


async def test_search_keeps_results_when_rerank_falls_back_unavailable() -> None:
    service = _search_service()
    retriever = _ready_search(service)
    chunk_id = uuid.uuid4()
    retriever.retrieve = AsyncMock(
        return_value=[CandidateHit(chunk_id, 0.04, CandidateSource.HYBRID)]
    )
    service._hydrator.hydrate = AsyncMock(
        return_value=[
            RetrievalResult(
                chunk_id=chunk_id,
                document_id=uuid.uuid4(),
                chunk_index=0,
                content="fallback",
                score=0.04,
                filename="doc.txt",
                metadata={"rerank_status": "unavailable", "reranker_provider": "cohere"},
            )
        ]
    )

    response = await service.search(SearchRequest(query="policy"))

    assert response.results
    assert response.diagnostics.rerank_status == "unavailable"
    assert response.diagnostics.embedding_identity_status == "matched"
