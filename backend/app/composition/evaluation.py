"""Evaluation composition across retrieval, conversations, providers, and jobs."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.composition.jobs import build_job_service
from app.composition.source_metadata import KnowledgeRetrievalSourceMetadataAdapter
from app.core.config import (
    QueryTranslationConfig,
    RerankerBackend,
    RerankMode,
    RetrievalConfig,
    RetrievalStrategy,
    Settings,
)
from app.modules.conversations.ports import ContextChunk, ContextRetrievalResult
from app.modules.conversations.prompts.registry import (
    GROUNDED_PROMPT_VERSION,
)
from app.modules.conversations.repositories.message_repository import MessageRepository
from app.modules.conversations.schemas.message import MessageSendRequest
from app.modules.conversations.services.message_execution_runner_service import (
    MessageExecutionRunner,
    _combine_token_counts,
)
from app.modules.conversations.turn_resolution import (
    normalize_request_scope,
    requires_complex_execution_budget,
)
from app.modules.evaluation.ports import (
    EvaluationAnswerPort,
    EvaluationCaseInput,
    EvaluationRetrievalPort,
    QualityAnswer,
    QualityCaseExecution,
    QualityHit,
    QualitySearchResult,
)
from app.modules.evaluation.repositories.evaluation_corpus_repository import (
    EvaluationCorpusRepository,
)
from app.modules.evaluation.repositories.evaluation_dataset_repository import (
    EvaluationDatasetRepository,
)
from app.modules.evaluation.repositories.evaluation_diagnostic_repository import (
    EvaluationDiagnosticRepository,
)
from app.modules.evaluation.repositories.evaluation_run_repository import EvaluationRunRepository
from app.modules.evaluation.services.evaluation_runner_service import EvaluationRunnerService
from app.modules.evaluation.services.evaluation_service import EvaluationService
from app.modules.retrieval.embedding_identity import EmbeddingIdentity
from app.modules.retrieval.known_dependency_gap import known_dependency_gap
from app.modules.retrieval.schemas.search import SearchRequest
from app.modules.retrieval.services.search_service import SearchService
from app.platform.config.project_ai import (
    EffectiveConfigResolution,
    SourcePolicyMode,
    apply_effective_ai_config,
    resolve_project_ai_config,
)
from app.platform.jobs.configuration import build_job_configuration
from app.platform.jobs.contracts import DurableJobSubmitter, JobQueue
from app.platform.jobs.implementations.job_queue_factory import create_job_queue
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
from app.platform.providers.contracts.llm import BaseLLMProvider
from app.platform.providers.contracts.query_translation import BaseQueryTranslationProvider
from app.platform.providers.contracts.reranker import BaseRerankerProvider
from app.platform.providers.errors import ProviderError, ProviderTimeoutError
from app.platform.providers.implementations.embedding_factory import (
    create_embedding_provider,
    create_embedding_provider_for_identity,
)
from app.platform.providers.implementations.embedding_reranker import EmbeddingRerankerProvider
from app.platform.providers.implementations.lexical_reranker import LexicalRerankerProvider
from app.platform.providers.implementations.llm_factory import create_llm_provider
from app.platform.providers.implementations.noop_reranker import NoopRerankerProvider
from app.platform.providers.implementations.query_translation_factory import (
    create_query_translation_provider,
)
from app.platform.providers.implementations.reranker_factory import create_reranker_provider
from app.platform.providers.request_work import ObservedLLM, RequestWork


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
        self._project_id = project_id
        self._session_factory = async_sessionmaker(
            bind=session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
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
            "semantic": self._service(
                session,
                project_id,
                embedder,
                NoopRerankerProvider(),
                retrieval_config=settings.retrieval.model_copy(
                    update={"strategy": RetrievalStrategy.SEMANTIC, "rerank_mode": RerankMode.OFF}
                ),
            ),
            "hybrid": self._service(
                session,
                project_id,
                embedder,
                NoopRerankerProvider(),
                retrieval_config=settings.retrieval.model_copy(
                    update={"strategy": RetrievalStrategy.HYBRID, "rerank_mode": RerankMode.OFF}
                ),
                query_translation_config=original_only,
            ),
            "multilingual_hybrid": self._service(
                session,
                project_id,
                embedder,
                NoopRerankerProvider(),
                retrieval_config=settings.retrieval.model_copy(
                    update={"strategy": RetrievalStrategy.HYBRID, "rerank_mode": RerankMode.OFF}
                ),
                query_translator=translator,
                query_translation_config=translated,
                persist_translation_text=True,
            ),
        }
        self._profile_metadata: dict[str, dict[str, Any]] = {
            "semantic": {"learned": False, "stage": "A"},
            "hybrid": {"learned": False, "stage": "B"},
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
                retrieval_config=settings.retrieval.model_copy(
                    update={
                        "strategy": RetrievalStrategy.HYBRID,
                        "rerank_mode": (
                            settings.retrieval.rerank_mode
                            if settings.retrieval.rerank_mode is not RerankMode.OFF
                            else RerankMode.ALWAYS
                        ),
                    }
                ),
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
                    and (embedder.provider_name != "hash" or backend is RerankerBackend.COHERE)
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
        if (
            retrieval.rerank_mode is RerankMode.OFF
            or retrieval.reranker_backend is RerankerBackend.NOOP
        ):
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
        response = await service.search(
            SearchRequest(
                query=query,
                top_k=top_k,
                document_id=document_id,
                metadata_filter=metadata_filter,
                as_of=as_of,
            )
        )
        return _quality_search_result(response)

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
        source_metadata: KnowledgeRetrievalSourceMetadataAdapter | None = None,
    ) -> SearchService:
        return SearchService(
            session=session,
            project_id=project_id,
            embedder=embedder,
            reranker=reranker,
            retrieval_config=retrieval_config or self._settings.retrieval,
            ai_policy=self._settings.ai_policy,
            source_metadata=source_metadata or self._source_metadata,
            configured_source_policy_mode=self._source_policy_mode,
            configuration_hash=self._configuration_hash,
            config_provenance=self._config_provenance,
            pinned_source_metadata_generation=self._source_metadata_generation,
            pinned_index_build_id=self._index_build_id,
            query_translator=query_translator,  # type: ignore[arg-type]
            query_translation_config=query_translation_config,
            persist_translation_text=persist_translation_text,
            query_embedder_factory=self._query_embedder_factory,
        )

    @asynccontextmanager
    async def case_search(self, profile: str) -> AsyncIterator[tuple[SearchService, AsyncSession]]:
        # This session owns only retrieval reads. Rolling it back never expires
        # the evaluation run/job retained by the worker's persistence session.
        prototype = self._services[profile]
        async with self._session_factory() as session:
            service = self._service(
                session,
                self._project_id,
                prototype._embedder,
                prototype._reranker,
                retrieval_config=prototype._config,
                query_translator=prototype._query_translator,
                query_translation_config=prototype._query_translation_config,
                persist_translation_text=prototype._persist_translation_text,
                source_metadata=KnowledgeRetrievalSourceMetadataAdapter(session),
            )
            yield service, session

    def _query_embedder_factory(self, identity: EmbeddingIdentity) -> BaseEmbeddingProvider:
        return create_embedding_provider_for_identity(
            self._settings,
            provider=identity.provider,
            model=identity.model,
            dimensions=identity.dimensions,
        )


def _quality_search_result(response: Any) -> QualitySearchResult:
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
                evidence_calibration_id=result.evidence_calibration_id,
                query_variants=result.query_variants,
                branch_contributions=result.branch_contributions,
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


class _EvaluationMessageRetrieval:
    """Production search/structural recall for a pinned evaluation profile."""

    supports_adjacent_retrieval = True
    supports_cited_retrieval = True
    supports_exact_recall = True

    def __init__(
        self,
        search: SearchService | None,
        hits: list[QualityHit],
        provenance: dict[str, Any],
        *,
        top_k: int | None = None,
    ):
        self.search = search
        self.initial = ContextRetrievalResult(
            chunks=[ContextChunk.from_retrieval_result(hit) for hit in hits],
            diagnostics=dict(provenance),
        )
        self.used_initial = False
        self.top_k = top_k
        self.first_search: QualitySearchResult | None = None
        self.query_embedder = search.resolved_query_embedder if search else None

    def set_request_scope(self, scope: dict[str, Any]) -> None:
        if self.search is not None:
            self.search.set_request_scope(scope)

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
        if (
            self.search is None
            and not self.used_initial
            and not adjacent_to
            and not cited_chunk_ids
        ):
            self.used_initial = True
            return self.initial
        if self.search is None:
            raise ProviderError(
                "Evaluation recovery search is unavailable",
                provider_name="retrieval",
                context={"reason": "evaluation_recovery_unavailable"},
            )
        result = await self.search.search(
            SearchRequest(
                query=query,
                top_k=self.top_k if not self.used_initial and self.top_k is not None else top_k,
                document_id=document_id,
                metadata_filter=metadata_filter or {},
                as_of=as_of,
            ),
            adjacent_to=adjacent_to,
            cited_chunk_ids=cited_chunk_ids,
        )
        if not self.used_initial and not adjacent_to and not cited_chunk_ids:
            self.used_initial = True
            self.first_search = _quality_search_result(result)
        diagnostics = result.diagnostics.model_dump(mode="json")
        diagnostics.update(
            known_dependency_gap(diagnostics, getattr(self.search, "_project_id", None))
        )
        return ContextRetrievalResult(
            chunks=[ContextChunk.from_retrieval_result(hit) for hit in result.results],
            diagnostics=diagnostics,
        )

    async def retrieve_exact(
        self,
        *,
        chunk_ids: list[uuid.UUID],
        query: str = "",
        document_id: uuid.UUID | None = None,
        metadata_filter: dict[str, str] | None = None,
        as_of: datetime | None = None,
    ) -> ContextRetrievalResult:
        if self.search is None:
            return ContextRetrievalResult(
                chunks=[], diagnostics={"identity_recall_status": "unavailable"}
            )
        result = await self.search.recall_indexed_identities(
            chunk_ids=chunk_ids,
            query=query,
            document_id=document_id,
            metadata_filter=metadata_filter,
            as_of=as_of,
        )
        return ContextRetrievalResult(
            chunks=[ContextChunk.from_retrieval_result(hit) for hit in result.results],
            diagnostics=result.diagnostics.model_dump(mode="json"),
        )


class GroundedEvaluationAnswerAdapter(EvaluationAnswerPort):
    """Execute production scope, recovery, drafting, verification and finalization."""

    def __init__(
        self,
        *,
        settings: Settings,
        llm: BaseLLMProvider,
        embedder: BaseEmbeddingProvider | None = None,
        domain_instructions: str = "",
        prompt_profile: str = "default",
        retrieval: SearchEvaluationAdapter | None = None,
        project_id: uuid.UUID | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._embedder = embedder
        self._domain_instructions = domain_instructions
        self._prompt_profile = prompt_profile
        self._retrieval = retrieval
        self._project_id = project_id or uuid.UUID(int=0)
        self._session = session

    async def _release_read_transaction(self) -> None:
        # Legacy in-memory answer() has no retrieval transaction to release.
        return None

    async def execute_case(
        self, *, profile: str, case: EvaluationCaseInput
    ) -> QualityCaseExecution:
        if self._retrieval is None:
            raise ValueError("Production evaluation requires retrieval composition")
        work = RequestWork(self._project_id)
        work.deadline = min(work.deadline, work.started + 50.0)
        async with self._retrieval.case_search(profile) as (service, session):
            retrieval = _EvaluationMessageRetrieval(service, [], {}, top_k=case.top_k)
            answer = await self.answer_for_case(
                profile=profile,
                request=MessageSendRequest(
                    content=case.query,
                    document_id=case.document_id,
                    metadata_filter=case.metadata_filter,
                    as_of=case.as_of,
                ),
                hits=[],
                provenance={},
                execution_retrieval=retrieval,
                work=work,
                release_read_transaction=session.rollback,
            )
            search = retrieval.first_search or QualitySearchResult(
                hits=[],
                latency_ms=work.timings.get("retrieval", 0),
                rerank_status="not_run",
                provenance={"status": "execution_ended_before_first_retrieval"},
            )
            return QualityCaseExecution(search=search, answer=answer)

    async def answer(self, *, profile: str, question: str, hits: list[QualityHit]) -> QualityAnswer:
        return await self.answer_for_case(
            profile=profile, request=MessageSendRequest(content=question), hits=hits, provenance={}
        )

    async def answer_for_case(
        self,
        *,
        profile: str,
        request: MessageSendRequest,
        hits: list[QualityHit],
        provenance: dict[str, Any],
        execution_retrieval: _EvaluationMessageRetrieval | None = None,
        work: RequestWork | None = None,
        release_read_transaction: Any = None,
    ) -> QualityAnswer:
        started = work.started if work is not None else time.perf_counter()
        service = self._retrieval._services.get(profile) if self._retrieval else None
        retrieval = execution_retrieval or _EvaluationMessageRetrieval(None, hits, provenance)
        work = work or RequestWork(self._project_id)
        work.configure_execution(
            self._settings.chat.execution_policy,
            complex_question=requires_complex_execution_budget(
                normalize_request_scope(request.content)
            ),
        )
        if work.execution_policy == "legacy":
            work.deadline = min(work.deadline, work.started + 50.0)
        runner = MessageExecutionRunner(
            project_id=self._project_id,
            retrieval=retrieval,
            chat_config=self._settings.chat,
            retrieval_config=service._config if service is not None else self._settings.retrieval,
            llm_config=self._settings.llm,
            release_read_transaction=release_read_transaction or self._release_read_transaction,
            embedder=self._embedder,
            domain_instructions=self._domain_instructions,
            prompt_profile=self._prompt_profile,
            work=work,
        )
        generation_ran = False
        generation_ms = 0
        prepared = None
        try:
            with work.attached():
                async with work.execution_timeout():
                    if work.execution_policy == "adaptive_v1" and self._session is not None:
                        identity = provenance.get("configuration_hash")
                        if identity is None and self._retrieval is not None:
                            identity = getattr(self._retrieval, "_configuration_hash", None)
                        if identity:
                            with work.attached(), work.stage("stage_estimate_read"):
                                samples = await MessageRepository(
                                    self._session, self._project_id
                                ).execution_samples(configuration_hash=str(identity))
                                work.freeze_measured_stages(samples)
                    prepared = await runner.prepare(
                        request=request,
                        current_message_id=uuid.uuid4(),
                        generation_history=[],
                        resolver_source=[],
                        citation_chunks={},
                        assistant_metadata_by_id={},
                        previous_assistant_metadata={},
                        llm=ObservedLLM(
                            self._llm, work, capacity=self._settings.llm.context_window_tokens
                        ),
                        temperature=self._settings.llm.temperature,
                    )
                    if prepared.preparation_error is not None:
                        raise prepared.preparation_error
                    generation_ran = (
                        bool(prepared.selected)
                        and prepared.non_knowledge_response is None
                        and prepared.clarification_response is None
                    )
                    generation_ms = 0
                    if generation_ran:
                        generation_started = time.perf_counter()
                        completion = await runner.generate(prepared)
                        generation_ms = round((time.perf_counter() - generation_started) * 1000)
                        content = completion.content
                        finish_reason = completion.finish_reason
                        provider, model = completion.provider, completion.model
                        input_tokens, output_tokens = _combine_token_counts(
                            prepared.resolver_usage,
                            completion.usage.input_tokens,
                            completion.usage.output_tokens,
                        )
                    else:
                        content = (
                            prepared.non_knowledge_response
                            or prepared.clarification_response
                            or runner._insufficient_content(prepared, request.content)
                        )
                        finish_reason = "insufficient_evidence"
                        provider, model = self._llm.provider_name, self._llm.model_name
                        input_tokens, output_tokens = _combine_token_counts(
                            prepared.resolver_usage, 0, 0
                        )
                    result = await runner.finalize(
                        prepared=prepared,
                        content=content,
                        finish_reason=finish_reason,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        provider=provider,
                        model=model,
                        generation_ms=generation_ms,
                        total_ms=round((time.perf_counter() - started) * 1000),
                        user_content_for_title=request.content,
                        streamed=False,
                        input_tokens_logged=input_tokens,
                        output_tokens_logged=output_tokens,
                        generation_ran=generation_ran,
                        non_knowledge_turn=prepared.non_knowledge_response is not None,
                        clarification_turn=prepared.clarification_response is not None,
                        insufficient_reason=prepared.evidence.reason
                        if not prepared.selected
                        else None,
                    )
        except (TimeoutError, ProviderTimeoutError) as exc:
            reason = (
                exc.context.get("reason", "provider_timeout")
                if isinstance(exc, ProviderTimeoutError)
                else "request_deadline_exceeded"
            )
            if reason not in {"request_deadline_exceeded", "recovery_deadline_exceeded"}:
                reason = "provider_timeout"
            result = runner.deadline_result(
                request,
                reason=reason,
                failure_phase=exc.context.get("phase")
                if isinstance(exc, ProviderTimeoutError)
                else None,
            )
        return QualityAnswer(
            answer=result.content,
            insufficient_evidence_reason=result.insufficient_evidence_reason,
            grounded=bool(result.grounded),
            citation_coverage=result.metadata.get("citation_coverage", 0.0),
            claims=result.claims,
            provider=result.provider,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            provider_latency_ms=generation_ms,
            generation_ran=generation_ran,
            selected_chunk_ids=[chunk.chunk_id for chunk in prepared.selected] if prepared else [],
            evidence_gate=result.metadata.get("evidence_gate", {}),
            execution=result.finalization.public_projection(),
            notices=result.metadata.get("notices", []),
            lifecycle=work.snapshot(),
            operator_diagnostic=result.finalization.model_dump(mode="json"),
            complete_turn_latency_ms=round((time.perf_counter() - started) * 1000),
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
        retrieval=retrieval,
        project_id=project_id,
        session=session,
    )
    return EvaluationRunnerService(
        runs=EvaluationRunRepository(session, project_id),
        datasets=EvaluationDatasetRepository(session, project_id),
        corpus=EvaluationCorpusRepository(session, project_id),
        retrieval=retrieval,
        answerer=answerer,
        config=settings.evaluation,
        diagnostics=EvaluationDiagnosticRepository(session, project_id),
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
        "prompt_version": GROUNDED_PROMPT_VERSION,
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


def _optional_translator(settings: Settings) -> BaseQueryTranslationProvider | None:
    try:
        return create_query_translation_provider(settings)
    except ProviderError:
        return None
