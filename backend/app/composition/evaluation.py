"""Evaluation composition across retrieval, conversations, providers, and jobs."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.composition.jobs import build_job_service
from app.core.config import RerankerBackend, RetrievalStrategy, Settings
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
from app.platform.domain.content_hash import content_hash
from app.platform.jobs.configuration import build_job_configuration
from app.platform.jobs.contracts import DurableJobSubmitter, JobQueue
from app.platform.jobs.implementations.job_queue_factory import create_job_queue
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.contracts.llm import BaseLLMProvider
from app.platform.providers.contracts.reranker import BaseRerankerProvider
from app.platform.providers.implementations.embedding_factory import create_embedding_provider
from app.platform.providers.implementations.embedding_reranker import EmbeddingRerankerProvider
from app.platform.providers.implementations.lexical_reranker import LexicalRerankerProvider
from app.platform.providers.implementations.llm_factory import create_llm_provider
from app.platform.providers.implementations.noop_reranker import NoopRerankerProvider


class SearchEvaluationAdapter(EvaluationRetrievalPort):
    """Run every profile through the production SearchService boundary."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        project_id: uuid.UUID,
        settings: Settings,
        embedder: BaseEmbeddingProvider,
    ) -> None:
        self._settings = settings
        self._services: dict[str, SearchService] = {
            "semantic": self._service(session, project_id, embedder, NoopRerankerProvider()),
            "hybrid": self._service(session, project_id, embedder, NoopRerankerProvider()),
        }
        self._profile_metadata: dict[str, dict[str, Any]] = {
            "semantic": {"learned": False},
            "hybrid": {"learned": False},
        }
        candidates = list(dict.fromkeys(settings.evaluation.reranker_candidates))
        if settings.retrieval.reranker_backend not in candidates:
            candidates.append(settings.retrieval.reranker_backend)
        for backend in candidates:
            if backend is RerankerBackend.NOOP:
                continue
            provider = _candidate_provider(backend, embedder)
            profile = f"reranked_{backend.value}"
            self._services[profile] = self._service(
                session,
                project_id,
                embedder,
                provider,
            )
            self._profile_metadata[profile] = {
                "provider": provider.provider_name,
                "model": provider.model_name,
                "version": provider.provider_version,
                "learned": (
                    backend in {RerankerBackend.EMBEDDING, RerankerBackend.EMBEDDING_MAX}
                    and embedder.provider_name != "hash"
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
            )
        )
        return QualitySearchResult(
            hits=[
                QualityHit(
                    chunk_id=result.chunk_id,
                    document_id=result.document_id,
                    content=result.content,
                    score=result.score,
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
        )

    def _service(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        embedder: BaseEmbeddingProvider,
        reranker: BaseRerankerProvider,
    ) -> SearchService:
        return SearchService(
            session=session,
            project_id=project_id,
            embedder=embedder,
            reranker=reranker,
            retrieval_config=self._settings.retrieval,
        )


class GroundedEvaluationAnswerAdapter(EvaluationAnswerPort):
    """Exercise the same prompt, context, refusal, and claim mapping as chat."""

    def __init__(self, *, settings: Settings, llm: BaseLLMProvider) -> None:
        self._settings = settings
        self._llm = llm
        self._context = ContextBuilder(settings.chat)
        self._prompt = PromptBuilder()
        self._grounding = GroundingService(settings.chat)

    async def answer(self, *, question: str, hits: list[QualityHit]) -> QualityAnswer:
        selected = self._context.select([_context_chunk(hit) for hit in hits])
        decision = self._grounding.assess(question, selected)
        if not decision.sufficient:
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
            )
        messages = self._prompt.build(
            template=require_prompt_template(self._settings.chat.system_prompt_version),
            context_chunks=selected,
            history=[],
            user_question=question,
        )
        completion = await self._llm.generate(
            messages,
            temperature=self._settings.llm.temperature,
            max_tokens=self._settings.llm.max_tokens,
        )
        result = self._grounding.map_claims(completion.content, selected)
        return QualityAnswer(
            answer=completion.content,
            insufficient_evidence_reason=None,
            grounded=result.grounded,
            citation_coverage=result.citation_coverage,
            claims=result.claims,
        )


def build_evaluation_service(
    *,
    session: AsyncSession,
    project_id: uuid.UUID,
    settings: Settings,
    submitter: DurableJobSubmitter | None = None,
    queue: JobQueue | None = None,
) -> EvaluationService:
    effective_queue = queue if queue is not None else create_job_queue(settings)
    effective_submitter = submitter or build_job_service(
        session=session,
        project_id=project_id,
        settings=settings,
        queue=effective_queue,
    )
    return EvaluationService(
        session=session,
        project_id=project_id,
        submitter=effective_submitter,
        job_configuration=build_job_configuration(settings),
        config=settings.evaluation,
        version_snapshot=build_quality_version_snapshot(settings),
        job_max_attempts=settings.jobs.max_attempts,
    )


def build_evaluation_runner(
    *,
    session: AsyncSession,
    project_id: uuid.UUID,
    settings: Settings,
    embedder: BaseEmbeddingProvider | None = None,
    llm: BaseLLMProvider | None = None,
) -> EvaluationRunnerService:
    effective_embedder = embedder or create_embedding_provider(settings)
    retrieval = SearchEvaluationAdapter(
        session=session,
        project_id=project_id,
        settings=settings,
        embedder=effective_embedder,
    )
    answerer = GroundedEvaluationAnswerAdapter(
        settings=settings,
        llm=llm or create_llm_provider(settings),
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
) -> BaseRerankerProvider:
    if backend is RerankerBackend.LEXICAL:
        return LexicalRerankerProvider()
    if backend is RerankerBackend.EMBEDDING:
        return EmbeddingRerankerProvider(embedder)
    if backend is RerankerBackend.EMBEDDING_MAX:
        return EmbeddingRerankerProvider(embedder, max_sentence=True)
    return NoopRerankerProvider()


def _context_chunk(hit: QualityHit) -> ContextChunk:
    return ContextChunk(
        chunk_id=hit.chunk_id,
        document_id=hit.document_id,
        chunk_index=hit.chunk_index,
        content=hit.content,
        score=hit.score,
        filename=hit.filename,
        chunk_hash=content_hash(hit.content),
        page_number=hit.page_number,
        char_start=hit.char_start,
        char_end=hit.char_end,
        metadata=hit.metadata,
    )
