"""FastAPI dependencies for the Conversations module."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Path

from app.core.config import get_settings
from app.dependencies.common import DbSessionDep
from app.dependencies.projects import ensure_project_exists, get_project_repository
from app.dependencies.retrieval import get_search_service
from app.modules.conversations.ports import ContextChunk, RetrievalPort
from app.modules.conversations.repositories.conversation_repository import ConversationRepository
from app.modules.conversations.repositories.message_repository import MessageRepository
from app.modules.conversations.services.chat_service import ChatService
from app.modules.conversations.services.conversation_service import ConversationService
from app.modules.projects.repositories.project_repository import ProjectRepository
from app.modules.retrieval.schemas.search import SearchRequest
from app.modules.retrieval.services.search_service import SearchService
from app.platform.domain.content_hash import content_hash


class SearchServiceRetrievalAdapter:
    """Maps retrieval SearchService to the conversations RetrievalPort."""

    def __init__(self, search_service: SearchService) -> None:
        self._search_service = search_service

    async def retrieve(
        self,
        *,
        query: str,
        top_k: int,
        document_id: uuid.UUID | None = None,
        metadata_filter: dict[str, str] | None = None,
    ) -> list[ContextChunk]:
        response = await self._search_service.search(
            SearchRequest(
                query=query,
                top_k=top_k,
                document_id=document_id,
                metadata_filter=metadata_filter or {},
            )
        )
        return [
            ContextChunk(
                chunk_id=result.chunk_id,
                document_id=result.document_id,
                chunk_index=result.chunk_index,
                content=result.content,
                score=result.score,
                filename=result.filename,
                chunk_hash=content_hash(result.content),
                page_number=result.page_number,
                char_start=result.char_start,
                char_end=result.char_end,
                metadata=dict(result.metadata),
            )
            for result in response.results
        ]


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


def get_conversation_service(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    conversation_repository: Annotated[
        ConversationRepository, Depends(get_conversation_repository)
    ],
    message_repository: Annotated[MessageRepository, Depends(get_message_repository)],
) -> ConversationService:
    settings = get_settings()

    async def ensure_project() -> None:
        await ensure_project_exists(project_repository, project_id)

    return ConversationService(
        session=session,
        project_id=project_id,
        conversation_repository=conversation_repository,
        message_repository=message_repository,
        llm_config=settings.llm,
        chat_config=settings.chat,
        ensure_project=ensure_project,
    )


def get_chat_service(
    session: DbSessionDep,
    project_id: Annotated[uuid.UUID, Path()],
    project_repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    conversation_repository: Annotated[
        ConversationRepository, Depends(get_conversation_repository)
    ],
    message_repository: Annotated[MessageRepository, Depends(get_message_repository)],
    retrieval: Annotated[RetrievalPort, Depends(get_retrieval_port)],
) -> ChatService:
    settings = get_settings()

    async def ensure_project() -> None:
        await ensure_project_exists(project_repository, project_id)

    return ChatService(
        session=session,
        project_id=project_id,
        conversation_repository=conversation_repository,
        message_repository=message_repository,
        retrieval=retrieval,
        settings=settings,
        chat_config=settings.chat,
        retrieval_config=settings.retrieval,
        llm_config=settings.llm,
        ensure_project=ensure_project,
    )


ConversationServiceDep = Annotated[ConversationService, Depends(get_conversation_service)]
ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]
