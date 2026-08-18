"""Evaluation composition across retrieval, conversations, providers, and jobs."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.composition.jobs import build_job_service
from app.composition.source_metadata import KnowledgeRetrievalSourceMetadataAdapter
from app.core.config import (
    EvidenceScoreMode,
    QueryTranslationConfig,
    RerankerBackend,
    RetrievalConfig,
    RetrievalStrategy,
    Settings,
)
from app.modules.conversations.context_builder import ContextBuilder
from app.modules.conversations.grounding_service import GroundingService
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.prompt_builder import PromptBuilder
from app.modules.conversations.prompts.registry import require_prompt_template
from app.modules.evaluation.ports import (
    EvaluationAnswerPort,
    EvaluationRetrievalPort,
    QualityAnswer,
    QualityHit,
    QualitySearchResult,
)
from app.modules.evaluation.repositories.evaluation_corpus_repository import (
    EvaluationCorpusRepository,
)
from app.modules.evaluation.repositories.evaluation_dataset_repository import (
    EvaluationDatasetRepository,
)
from app.modules.evaluation.repositories.evaluation_run_repository import EvaluationRunRepository
from app.modules.evaluation.services.evaluation_runner_service import EvaluationRunnerService
from app.modules.evaluation.services.evaluation_service import EvaluationService
from app.modules.retrieval.schemas.search import SearchRequest
from app.modules.retrieval.services.search_service import SearchService
from app.platform.config.project_ai import (
    EffectiveConfigResolution,
    SourcePolicyMode,
    apply_effective_ai_config,
    resolve_project_ai_config,
)
from app.platform.domain.content_hash import content_hash
from app.platform.jobs.configuration import build_job_configuration
from app.platform.jobs.contracts import DurableJobSubmitter, JobQueue
from app.platform.jobs.implementations.job_queue_factory import create_job_queue
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.contracts.llm import BaseLLMProvider
from app.platform.providers.contracts.reranker import BaseRerankerProvider
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.embedding_factory import create_embedding_provider
from app.platform.providers.implementations.embedding_reranker import EmbeddingRerankerProvider
from app.platform.providers.implementations.lexical_reranker import LexicalRerankerProvider
from app.platform.providers.implementations.llm_factory import create_llm_provider
from app.platform.providers.implementations.noop_reranker import NoopRerankerProvider
from app.platform.providers.implementations.query_translation_factory import (
    create_query_translation_provider,
)
from app.platform.providers.implementations.reranker_factory import create_reranker_provider


class SearchEvaluationAdapter(EvaluationRetrievalPort):
    """Run every profile through the production SearchService boundary."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        project_id: uuid.UUID,
        settings: Settings,
        embedder: BaseEmbeddingProvider,
        source_policy_mode: SourcePolicyMode = SourcePolicyMode.OFF,
        source_metadata_generation: int | None = None,
        index_build_id: uuid.UUID | None = None,
        configuration_hash: str | None = None,
        config_provenance: dict[str, Any] | None = None,
    ) -> None:
        self._settings = settings
        self._source_metadata = KnowledgeRetrievalSourceMetadataAdapter(session)
        self._source_policy_mode = source_policy_mode
        self._source_metadata_generation = source_metadata_generation
        self._index_build_id = index_build_id
        self._configuration_hash = configuration_hash
        self._config_provenance = config_provenance or {}
        translator = _optional_translator(settings)
        original_only = settings.query_translation.model_copy(update={"enabled": False})
        translated = settings.query_translation.model_copy(update={"enabled": True})
        self._services: dict[str, SearchService] = {
            "semantic": self._service(session, project_id, embedder, NoopRerankerProvider()),
            "hybrid": self._service(
                session,
                project_id,
                embedder,
                NoopRerankerProvider(),
                query_translation_config=original_only,
            ),
            "passage": self._service(
                session,
                project_id,
                embedder,
                NoopRerankerProvider(),
                retrieval_config=settings.retrieval.model_copy(
                    update={"passage_scoring_enabled": True}
                ),
                query_translation_config=original_only,
            ),
            "multilingual_hybrid": self._service(
                session,
                project_id,
                embedder,
                NoopRerankerProvider(),
                query_translator=translator,
                query_translation_config=translated,
                persist_translation_text=True,
            ),
        }
        self._profile_metadata: dict[str, dict[str, Any]] = {
            "semantic": {"learned": False, "stage": "A"},
            "hybrid": {"learned": False, "stage": "B"},
            "passage": {
                "learned": embedder.provider_name != "hash",
                "evidence_score_method": "bounded_token_max_v1",
            },
            "multilingual_hybrid": {
                "learned": False,
                "stage": "E",
                "translation": translator is not None,
            },
        }
        candidates = list(dict.fromkeys(settings.evaluation.reranker_candidates))
        if settings.retrieval.reranker_backend not in candidates:
            candidates.append(settings.retrieval.reranker_backend)
        for backend in candidates:
            if backend is RerankerBackend.NOOP:
                continue
            provider = _candidate_provider(backend, embedder, settings)
            profile = f"reranked_{backend.value}"
            self._services[profile] = self._service(
                session,
                project_id,
                embedder,
                provider,
                query_translator=translator,
                query_translation_config=translated,
                persist_translation_text=True,
            )
            self._profile_metadata[profile] = {
                "provider": provider.provider_name,
                "model": provider.model_name,
                "version": provider.provider_version,
                "stage": "F" if backend is RerankerBackend.COHERE else None,
                "learned": (
                    backend
                    in {
                        RerankerBackend.EMBEDDING,
                        RerankerBackend.EMBEDDING_MAX,
                        RerankerBackend.COHERE,
                    }
                    and (
                        embedder.provider_name != "hash"
                        or backend is RerankerBackend.COHERE
                    )
                ),
            }

    @property
    def profiles(self) -> tuple[str, ...]:
        return tuple(self._services)

    @property
    def primary_profile(self) -> str:
        retrieval = self._settings.retrieval
        if retrieval.strategy is RetrievalStrategy.SEMANTIC:
            return "semantic"
        if self._settings.chat.evidence_score_mode is EvidenceScoreMode.PASSAGE_MAX:
            return "passage"
        if not retrieval.rerank_enabled or retrieval.reranker_backend is RerankerBackend.NOOP:
            return "hybrid"
        profile = f"reranked_{retrieval.reranker_backend.value}"
        return profile if profile in self._services else "hybrid"

    @property
    def profile_metadata(self) -> dict[str, dict[str, Any]]:
        return self._profile_metadata

    async def search(
        self,
        *,
        profile: str,
        query: str,
        top_k: int,
        document_id: uuid.UUID | None,
        metadata_filter: dict[str, str],
        as_of: datetime | None,
    ) -> QualitySearchResult:
        service = self._services[profile]
        semantic = profile == "semantic"
        response = await service.search(
            SearchRequest(
                query=query,
                top_k=top_k,
                document_id=document_id,
                metadata_filter=metadata_filter,
                strategy=(RetrievalStrategy.SEMANTIC if semantic else RetrievalStrategy.HYBRID),
                rerank=profile.startswith("reranked_"),
                as_of=as_of,
            )
        )
        return QualitySearchResult(
            hits=[
                QualityHit(
                    chunk_id=result.chunk_id,
                    document_id=result.document_id,
                    content=result.content,
                    score=result.score,
                    semantic_score=result.semantic_score,
                    rank_score=result.rank_score,
                    rerank_relevance_score=result.rerank_relevance_score,
                    passage_semantic_score=result.passage_semantic_score,
                    passage_char_start=result.passage_char_start,
                    passage_char_end=result.passage_char_end,
                    passage_score_method=result.passage_score_method,
                    filename=result.filename,
                    chunk_index=result.chunk_index,
                    page_number=result.page_number,
                    char_start=result.char_start,
                    char_end=result.char_end,
                    metadata=dict(result.metadata),
                )
                for result in response.results
            ],
            latency_ms=response.diagnostics.duration_ms,
            rerank_status=response.diagnostics.rerank_status,
            reranker_provider=response.diagnostics.reranker_provider,
            reranker_model=response.diagnostics.reranker_model,
            reranker_version=response.diagnostics.reranker_version,
            reranker_score_scale=response.diagnostics.reranker_score_scale,
            provenance=response.diagnostics.model_dump(mode="json"),
        )

    def _service(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        embedder: BaseEmbeddingProvider,
        reranker: BaseRerankerProvider,
        retrieval_config: RetrievalConfig | None = None,
        query_translator: object | None = None,
        query_translation_config: QueryTranslationConfig | None = None,
        persist_translation_text: bool = False,
    ) -> SearchService:
        return SearchService(
            session=session,
            project_id=project_id,
            embedder=embedder,
            reranker=reranker,
            retrieval_config=retrieval_config or self._settings.retrieval,
            ai_policy=self._settings.ai_policy,
            source_metadata=self._source_metadata,
            configured_source_policy_mode=self._source_policy_mode,
            configuration_hash=self._configuration_hash,
            config_provenance=self._config_provenance,
            pinned_source_metadata_generation=self._source_metadata_generation,
            pinned_index_build_id=self._index_build_id,
            query_translator=query_translator,  # type: ignore[arg-type]
            query_translation_config=query_translation_config,
            persist_translation_text=persist_translation_text,
        )


class GroundedEvaluationAnswerAdapter(EvaluationAnswerPort):
    """Exercise the same prompt, context, refusal, and claim mapping as chat."""

    def __init__(
        self,
        *,
        settings: Settings,
        llm: BaseLLMProvider,
        embedder: BaseEmbeddingProvider | None = None,
        domain_instructions: str = "",
        prompt_profile: str = "default",
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._context = ContextBuilder(settings.chat)
        self._prompt = PromptBuilder()
        translator = _optional_translator(settings)
        translation_config = settings.query_translation
        self._whole_chunk_grounding = GroundingService(
            settings.chat.model_copy(update={"evidence_score_mode": EvidenceScoreMode.WHOLE_CHUNK}),
            embedder=embedder,
            translator=translator,
            translation_config=translation_config,
        )
        self._passage_grounding = GroundingService(
            settings.chat.model_copy(update={"evidence_score_mode": EvidenceScoreMode.PASSAGE_MAX}),
            embedder=embedder,
            translator=translator,
            translation_config=translation_config,
        )
        self._domain_instructions = domain_instructions
        self._prompt_profile = prompt_profile

    async def answer(
        self,
        *,
        profile: str,
        question: str,
        hits: list[QualityHit],
    ) -> QualityAnswer:
        chunks = [_context_chunk(hit) for hit in hits]
        selected = self._context.select(chunks)
        selected_ids = {chunk.chunk_id for chunk in selected}
        grounding = (
            self._passage_grounding if profile == "passage" else self._whole_chunk_grounding
        )
        decision = grounding.assess(
            question,
            [chunk for chunk in chunks if chunk.chunk_id in selected_ids],
        )
        blocked = grounding.blocks_generation(decision)
        if blocked:
            return QualityAnswer(
                answer=self._settings.chat.insufficient_evidence_message,
                insufficient_evidence_reason=(
                    decision.reason.value
                    if decision.reason is not None
                    else "insufficient_evidence"
                ),
                grounded=False,
                citation_coverage=1.0,
                claims=[],
                provider=self._llm.provider_name,
                model=self._llm.model_name,
                input_tokens=0,
                output_tokens=0,
                provider_latency_ms=0,
                generation_ran=False,
                selected_chunk_ids=[chunk.chunk_id for chunk in selected],
                evidence_gate=grounding.diagnostics(
                    decision,
                    blocked_generation=True,
                    generation_ran=False,
                ),
            )
        messages = self._prompt.build(
            template=require_prompt_template(self._settings.chat.system_prompt_version),
            context_chunks=selected,
            history=[],
            user_question=question,
            domain_instructions=self._domain_instructions,
            prompt_profile=self._prompt_profile,
        )
        provider_started = time.perf_counter()
        completion = await self._llm.generate(
            messages,
            temperature=self._settings.llm.temperature,
            max_tokens=self._settings.llm.max_tokens,
        )
        provider_latency_ms = int((time.perf_counter() - provider_started) * 1000)
        result = await grounding.map_claims(completion.content, selected)
        return QualityAnswer(
            answer=completion.content,
            insufficient_evidence_reason=None,
            grounded=result.grounded,
            citation_coverage=result.citation_coverage,
            claims=result.claims,
            provider=completion.provider,
            model=completion.model,
            input_tokens=completion.usage.input_tokens,
            output_tokens=completion.usage.output_tokens,
            provider_latency_ms=provider_latency_ms,
            generation_ran=True,
            selected_chunk_ids=[chunk.chunk_id for chunk in selected],
            evidence_gate=grounding.diagnostics(
                decision,
                blocked_generation=False,
                generation_ran=True,
            ),
        )


def build_evaluation_service(
    *,
    session: AsyncSession,
    project_id: uuid.UUID,
    settings: Settings,
    submitter: DurableJobSubmitter | None = None,
    queue: JobQueue | None = None,
    resolution: EffectiveConfigResolution | None = None,
    source_metadata_generation: int = 0,
) -> EvaluationService:
    effective_resolution = resolution or resolve_project_ai_config(settings, None)
    effective_settings = apply_effective_ai_config(settings, effective_resolution)
    effective_queue = queue if queue is not None else create_job_queue(effective_settings)
    effective_submitter = submitter or build_job_service(
        session=session,
        project_id=project_id,
        settings=effective_settings,
        queue=effective_queue,
    )
    return EvaluationService(
        session=session,
        project_id=project_id,
        submitter=effective_submitter,
        job_configuration=build_job_configuration(
            settings,
            resolution=effective_resolution,
            source_metadata_generation=source_metadata_generation,
        ),
        config=effective_settings.evaluation,
        version_snapshot=build_quality_version_snapshot(effective_settings),
        job_max_attempts=effective_settings.jobs.max_attempts,
        execution_snapshot=effective_resolution.secret_free_snapshot(),
        execution_provenance=effective_resolution.provenance.model_dump(mode="json"),
    )


def build_evaluation_runner(
    *,
    session: AsyncSession,
    project_id: uuid.UUID,
    settings: Settings,
    embedder: BaseEmbeddingProvider | None = None,
    llm: BaseLLMProvider | None = None,
    source_policy_mode: SourcePolicyMode = SourcePolicyMode.OFF,
    source_metadata_generation: int | None = None,
    index_build_id: uuid.UUID | None = None,
    configuration_hash: str | None = None,
    config_provenance: dict[str, Any] | None = None,
    domain_instructions: str = "",
    prompt_profile: str = "default",
) -> EvaluationRunnerService:
    effective_embedder = embedder or create_embedding_provider(settings)
    retrieval = SearchEvaluationAdapter(
        session=session,
        project_id=project_id,
        settings=settings,
        embedder=effective_embedder,
        source_policy_mode=source_policy_mode,
        source_metadata_generation=source_metadata_generation,
        index_build_id=index_build_id,
        configuration_hash=configuration_hash,
        config_provenance=config_provenance,
    )
    answerer = GroundedEvaluationAnswerAdapter(
        settings=settings,
        llm=llm or create_llm_provider(settings),
        embedder=effective_embedder,
        domain_instructions=domain_instructions,
        prompt_profile=prompt_profile,
    )
    return EvaluationRunnerService(
        runs=EvaluationRunRepository(session, project_id),
        datasets=EvaluationDatasetRepository(session, project_id),
        corpus=EvaluationCorpusRepository(session, project_id),
        retrieval=retrieval,
        answerer=answerer,
        config=settings.evaluation,
    )


def build_quality_version_snapshot(settings: Settings) -> dict[str, Any]:
    return {
        "application_version": settings.app.version,
        "chunking": settings.chunking.model_dump(mode="json"),
        "retrieval": settings.retrieval.model_dump(mode="json"),
        "chat": settings.chat.model_dump(mode="json"),
        "embedding": settings.embedding.model_dump(
            mode="json",
            exclude={"openai_api_key", "gemini_api_key"},
        ),
        "reranker": {
            "backend": settings.retrieval.reranker_backend.value,
        },
        "llm": settings.llm.model_dump(
            mode="json",
            exclude={"openai_api_key", "gemini_api_key"},
        ),
        "prompt_version": settings.chat.system_prompt_version,
        "evaluator_version": settings.evaluation.evaluator_version,
    }


def _candidate_provider(
    backend: RerankerBackend,
    embedder: BaseEmbeddingProvider,
    settings: Settings,
) -> BaseRerankerProvider:
    if backend is RerankerBackend.LEXICAL:
        return LexicalRerankerProvider()
    if backend is RerankerBackend.EMBEDDING:
        return EmbeddingRerankerProvider(embedder)
    if backend is RerankerBackend.EMBEDDING_MAX:
        return EmbeddingRerankerProvider(embedder, max_sentence=True)
    if backend is RerankerBackend.COHERE:
        try:
            return create_reranker_provider(settings, backend=RerankerBackend.COHERE)
        except ProviderError:
            return NoopRerankerProvider()
    return NoopRerankerProvider()


def _optional_translator(settings: Settings):
    try:
        return create_query_translation_provider(settings)
    except ProviderError:
        return None


def _context_chunk(hit: QualityHit) -> ContextChunk:
    return ContextChunk(
        chunk_id=hit.chunk_id,
        document_id=hit.document_id,
        chunk_index=hit.chunk_index,
        content=hit.content,
        score=hit.score,
        filename=hit.filename,
        chunk_hash=content_hash(hit.content),
        semantic_score=hit.semantic_score,
        rank_score=hit.rank_score,
        rerank_relevance_score=_optional_hit_float(hit.rerank_relevance_score),
        passage_semantic_score=hit.passage_semantic_score,
        passage_char_start=hit.passage_char_start,
        passage_char_end=hit.passage_char_end,
        passage_score_method=hit.passage_score_method,
        page_number=hit.page_number,
        char_start=hit.char_start,
        char_end=hit.char_end,
        metadata=hit.metadata,
    )


def _optional_hit_float(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None
