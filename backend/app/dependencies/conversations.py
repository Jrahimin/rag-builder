"""FastAPI dependencies for the Conversations module."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Path

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
from app.modules.retrieval.schemas.search import RetrievalResult, SearchRequest
from app.modules.retrieval.services.search_service import SearchService
from app.platform.config.project_ai import (
    ConfigProvenance,
    ConfigRevisionRecord,
    EffectiveConfigResolution,
    EffectiveProjectAIConfig,
    apply_effective_ai_config,
    resolve_project_ai_config,
)
from app.platform.domain.content_hash import content_hash
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider
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


class SearchServiceRetrievalAdapter:
    """Maps retrieval SearchService to the conversations RetrievalPort."""

    def __init__(self, search_service: SearchService) -> None:
        self._search_service = search_service

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
    ) -> ContextRetrievalResult:
        response = await self._search_service.search(
            SearchRequest(
                query=query,
                top_k=top_k,
                document_id=document_id,
                metadata_filter=metadata_filter or {},
                as_of=as_of,
            )
        )
        return ContextRetrievalResult(
            chunks=[_context_chunk_from_result(result) for result in response.results],
            diagnostics=response.diagnostics.model_dump(mode="json"),
        )


def _context_chunk_from_result(result: RetrievalResult) -> ContextChunk:
    """Map a search hit onto chat context without dropping rerank scores."""
    rerank_score = result.rerank_relevance_score
    rank_score = result.rank_score
    evidence_score = result.evidence_relevance_score
    if result.metadata.get("rerank_status") == "applied":
        if rerank_score is None:
            rerank_score = result.score
        if rank_score is None:
            rank_score = result.score
        if evidence_score is None:
            evidence_score = rerank_score
    return ContextChunk(
        chunk_id=result.chunk_id,
        document_id=result.document_id,
        chunk_index=result.chunk_index,
        content=result.content,
        score=result.score,
        filename=result.filename,
        chunk_hash=content_hash(result.content),
        semantic_score=result.semantic_score,
        rank_score=rank_score,
        rerank_relevance_score=rerank_score,
        evidence_relevance_score=evidence_score,
        evidence_score_method=result.evidence_score_method
        or ("reranker_relevance" if rerank_score is not None else None),
        evidence_calibration_id=result.evidence_calibration_id,
        passage_semantic_score=result.passage_semantic_score,
        passage_char_start=result.passage_char_start,
        passage_char_end=result.passage_char_end,
        passage_score_method=result.passage_score_method,
        page_number=result.page_number,
        char_start=result.char_start,
        char_end=result.char_end,
        metadata=dict(result.metadata),
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
        active_revision=(
            ConfigRevisionRecord(
                id=revision.id,
                revision_number=revision.revision_number,
                configuration_hash=revision.configuration_hash,
                configuration=dict(revision.configuration),
            )
            if revision is not None
            else None
        ),
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
            ConfigRevisionRecord(
                id=revision.id,
                revision_number=revision.revision_number,
                configuration_hash=revision.configuration_hash,
                configuration=dict(revision.configuration),
            )
            if revision is not None
            else None,
        )
        snapshot_id = None
    else:
        resolution = EffectiveConfigResolution(
            configuration=EffectiveProjectAIConfig.model_validate(snapshot.configuration),
            configuration_hash=snapshot.configuration_hash,
            origins=dict(snapshot.origins),
            provenance=ConfigProvenance.model_validate(snapshot.provenance),
            compatibility_diagnostics=list(snapshot.compatibility_diagnostics),
        )
        snapshot_id = snapshot.id
    effective_settings = apply_effective_ai_config(settings, resolution)
    web_search: BaseWebSearchProvider | None = None
    if effective_settings.chat.response_mode is not ResponseMode.INDEXED_ONLY:
        web_search = create_web_search_provider(effective_settings)
    reranker = create_reranker_provider(effective_settings)
    translator = None
    if effective_settings.query_translation.enabled:
        try:
            translator = create_query_translation_provider(effective_settings)
        except ProviderError:
            translator = None
    retrieval = SearchServiceRetrievalAdapter(
        SearchService(
            session=session,
            project_id=project_id,
            embedder=embedder,
            reranker=reranker,
            retrieval_config=effective_settings.retrieval,
            ai_policy=settings.ai_policy,
            source_metadata=KnowledgeRetrievalSourceMetadataAdapter(session),
            configured_source_policy_mode=(resolution.provenance.configured_source_policy_mode),
            configuration_hash=resolution.configuration_hash,
            config_provenance=resolution.provenance.model_dump(mode="json"),
            query_translator=translator,
            query_translation_config=effective_settings.query_translation,
            query_embedder_factory=query_embedder_factory_for(settings),
        )
    )

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
        embedder=embedder,
        web_search=web_search,
        web_search_config=effective_settings.web_search,
    )


ConversationServiceDep = Annotated[ConversationService, Depends(get_conversation_service)]
ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]
