"""FastAPI dependencies for the Conversations module."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.composition.audit import DatabaseAuditRecorder
from app.composition.source_metadata import KnowledgeRetrievalSourceMetadataAdapter
from app.core.config import ResponseMode, get_settings
from app.dependencies.access import AdminOrOrganizationDep
from app.dependencies.common import DbSessionDep
from app.dependencies.retrieval import get_search_service, query_embedder_factory_for
from app.models.conversation import Conversation
from app.modules.conversations.ports import ContextChunk, ContextRetrievalResult, RetrievalPort
from app.modules.conversations.repositories.config_snapshot_repository import (
    ConversationConfigSnapshotRepository,
)
from app.modules.conversations.repositories.conversation_repository import ConversationRepository
from app.modules.conversations.repositories.message_repository import MessageRepository
from app.modules.conversations.services.chat_service import ChatService
from app.modules.conversations.services.conversation_service import ConversationService
from app.modules.projects.repositories.project_ai_config_repository import (
    ProjectAIConfigRepository,
)
from app.modules.retrieval.schemas.search import SearchRequest
from app.modules.retrieval.services.search_service import SearchService
from app.platform.config.project_ai import (
    ConfigProvenance,
    EffectiveConfigResolution,
    EffectiveProjectAIConfig,
    InvariantState,
    StructuredOrigin,
    apply_effective_ai_config,
    config_revision_record,
    resolve_project_ai_config,
)
from app.platform.infra.recovery_capacity import recovery_slot
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider, EmbeddingPurpose
from app.platform.providers.contracts.llm import BaseLLMProvider
from app.platform.providers.contracts.web_search import BaseWebSearchProvider
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.embedding_factory import get_embedding_provider
from app.platform.providers.implementations.llm_factory import (
    create_llm_provider_for_conversation,
)
from app.platform.providers.implementations.query_translation_factory import (
    create_query_translation_provider,
)
from app.platform.providers.implementations.reranker_factory import create_reranker_provider
from app.platform.providers.implementations.web_search_factory import (
    create_web_search_provider,
)
from app.platform.providers.request_work import CachedEmbeddingProvider, RequestWork


class SearchServiceRetrievalAdapter:
    """Maps retrieval SearchService to the conversations RetrievalPort."""

    supports_adjacent_retrieval = True
    supports_batch_retrieval = True

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

    async def retrieve_batch(
        self, requests: list[dict[str, Any]], *, snapshot: dict[str, Any]
    ) -> list[ContextRetrievalResult]:
        if self._branch_factory is None or self._session_factory is None:
            return [await self.retrieve(**request) for request in requests]
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
        limiter = asyncio.Semaphore(3)

        async def branch(request: dict[str, Any]) -> ContextRetrievalResult:
            async with limiter, _RECOVERY_LIMIT, self._deployment_slot():  # noqa: SIM117
                async with session_factory() as session:
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
        try:
            # gather preserves planned order. Failure cancels siblings; no lost dependency.
            return list(await asyncio.gather(*tasks))
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    @asynccontextmanager
    async def _deployment_slot(self) -> AsyncIterator[None]:
        if self._redis_dsn is None:
            yield
        else:
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
    ) -> ContextRetrievalResult:
        response = await self._search_service.search(
            SearchRequest(
                query=query,
                top_k=top_k,
                document_id=document_id,
                metadata_filter=metadata_filter or {},
                as_of=as_of,
            ),
            adjacent_to=adjacent_to,
        )
        return ContextRetrievalResult(
            chunks=[ContextChunk.from_retrieval_result(result) for result in response.results],
            diagnostics=response.diagnostics.model_dump(mode="json"),
        )


def get_conversation_repository(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
) -> ConversationRepository:
    return ConversationRepository(session, project_id)


def get_message_repository(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
) -> MessageRepository:
    return MessageRepository(session, project_id)


def get_retrieval_port(
    search_service: Annotated[SearchService, Depends(get_search_service)],
) -> RetrievalPort:
    return SearchServiceRetrievalAdapter(search_service)


async def get_conversation_service(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
    conversation_repository: Annotated[
        ConversationRepository, Depends(get_conversation_repository)
    ],
    message_repository: Annotated[MessageRepository, Depends(get_message_repository)],
    auth_org: AdminOrOrganizationDep,
) -> ConversationService:
    settings = get_settings()
    revision = await ProjectAIConfigRepository(session, project_id).get_active()

    return ConversationService(
        session=session,
        project_id=project_id,
        conversation_repository=conversation_repository,
        message_repository=message_repository,
        llm_config=settings.llm,
        chat_config=settings.chat,
        settings=settings,
        active_revision=config_revision_record(revision),
        actor_id=(
            "platform_admin"
            if auth_org.is_platform_admin
            else str(auth_org.api_key_id or "auth-bypassed")
        ),
        audit=DatabaseAuditRecorder(session, project_id),
    )


async def get_chat_service(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
    conversation_repository: Annotated[
        ConversationRepository, Depends(get_conversation_repository)
    ],
    message_repository: Annotated[MessageRepository, Depends(get_message_repository)],
    conversation_id: Annotated[uuid.UUID, Path()],
    embedder: Annotated[BaseEmbeddingProvider, Depends(get_embedding_provider)],
) -> ChatService:
    work = RequestWork(project_id)
    snapshot_started = time.perf_counter()
    settings = get_settings()
    conversation = await conversation_repository.get_by_id(conversation_id, include_deleted=True)
    snapshot = (
        await ConversationConfigSnapshotRepository(session, project_id).get(
            conversation.active_config_snapshot_id
        )
        if conversation is not None and conversation.active_config_snapshot_id is not None
        else None
    )
    if snapshot is None:
        revision = await ProjectAIConfigRepository(session, project_id).get_active()
        resolution = resolve_project_ai_config(
            settings,
            config_revision_record(revision),
            # Provider availability is runtime state. Preserve the response policy here,
            # then let ChatService fail closed only if a turn actually needs web evidence.
            validate_web_provider=False,
        )
        snapshot_id = None
    else:
        resolution = EffectiveConfigResolution(
            configuration=EffectiveProjectAIConfig.model_validate(snapshot.configuration),
            configuration_hash=snapshot.configuration_hash,
            effective_value_hash=snapshot.configuration_hash,
            resolution_fingerprint=(snapshot.resolution_fingerprint or snapshot.configuration_hash),
            origins=dict(snapshot.origins),
            structured_origins={
                path: StructuredOrigin.model_validate(value)
                for path, value in (snapshot.structured_origins or {}).items()
            },
            provenance=ConfigProvenance.model_validate(snapshot.provenance),
            invariants=InvariantState.model_validate(snapshot.invariants),
            compatibility_diagnostics=list(snapshot.compatibility_diagnostics),
        )
        snapshot_id = snapshot.id
    effective_settings = apply_effective_ai_config(settings, resolution)
    web_search: BaseWebSearchProvider | None = None
    if effective_settings.chat.response_mode is not ResponseMode.INDEXED_ONLY:
        try:
            web_search = create_web_search_provider(effective_settings)
        except ProviderError:
            # Keep knowledge-backed turns available. ChatService turns this into a
            # fail-closed, user-friendly no-answer only if web evidence is required.
            web_search = None
    reranker = create_reranker_provider(effective_settings)
    translator = None
    if effective_settings.query_translation.enabled:
        try:
            translator = create_query_translation_provider(effective_settings)
        except ProviderError:
            translator = None

    def build_search(
        db_session: AsyncSession, pinned: dict[str, Any] | None = None
    ) -> SearchService:
        return SearchService(
            session=db_session,
            project_id=project_id,
            embedder=embedder,
            reranker=reranker,
            retrieval_config=effective_settings.retrieval,
            ai_policy=settings.ai_policy,
            source_metadata=KnowledgeRetrievalSourceMetadataAdapter(db_session),
            configured_source_policy_mode=(resolution.provenance.configured_source_policy_mode),
            configuration_hash=resolution.configuration_hash,
            config_provenance=resolution.provenance.model_dump(mode="json"),
            query_translator=translator if pinned is None else None,
            query_translation_config=(
                effective_settings.query_translation
                if pinned is None
                else effective_settings.query_translation.model_copy(update={"enabled": False})
            ),
            query_embedder_factory=query_embedder_factory_for(settings),
            pinned_index_build_id=uuid.UUID(pinned["index_build_id"]) if pinned else None,
            pinned_source_metadata_generation=pinned["source_metadata_generation"]
            if pinned
            else None,
            pinned_reference_date=pinned.get("reference_date") if pinned else None,
            work=work,
        )

    retrieval = SearchServiceRetrievalAdapter(
        build_search(session),
        branch_factory=build_search,
        session_factory=async_sessionmaker(bind=session.bind, expire_on_commit=False),
        redis_dsn=settings.redis.dsn,
    )
    work.timings["snapshot_loading"] = round((time.perf_counter() - snapshot_started) * 1000)

    def resolve_llm(conversation: Conversation) -> BaseLLMProvider:
        return create_llm_provider_for_conversation(
            settings,
            provider=conversation.provider,
            model=conversation.model,
        )

    return ChatService(
        session=session,
        project_id=project_id,
        conversation_repository=conversation_repository,
        message_repository=message_repository,
        retrieval=retrieval,
        chat_config=effective_settings.chat,
        retrieval_config=effective_settings.retrieval,
        llm_config=effective_settings.llm,
        resolve_llm=resolve_llm,
        config_snapshot_id=snapshot_id,
        config_provenance=resolution.provenance.model_dump(mode="json"),
        domain_instructions=resolution.configuration.domain_instructions,
        prompt_profile=resolution.configuration.prompt_profile,
        evidence_approach=resolution.configuration.evidence_approach,
        translation_enabled=effective_settings.query_translation.enabled,
        work=work,
        embedder=embedder,
        web_search=web_search,
        web_search_config=effective_settings.web_search,
    )


ConversationServiceDep = Annotated[ConversationService, Depends(get_conversation_service)]
ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]

# Worker-process wide bound in addition to the per-turn concurrency of three.
_RECOVERY_LIMIT = asyncio.Semaphore(12)
