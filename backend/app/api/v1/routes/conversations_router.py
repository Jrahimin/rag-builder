"""Conversation HTTP routes."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import structlog
from fastapi import APIRouter, Query, Request, status
from fastapi.responses import StreamingResponse

from app.core.exceptions import APEError
from app.core.http.envelopes import ApiResponse
from app.dependencies.conversations import ChatServiceDep, ConversationServiceDep
from app.modules.conversations.schemas.conversation import (
    ConversationCreate,
    ConversationResponse,
    ConversationUpdate,
)
from app.modules.conversations.schemas.message import (
    ChatTurnResponse,
    MessageResponse,
    MessageSendRequest,
)
from app.platform.http.pagination import ListParams, PaginatedResult
from app.platform.providers.errors import ProviderError

router = APIRouter()
logger = structlog.get_logger(__name__)


def _message_response(message: object, conversation: object) -> MessageResponse:
    return MessageResponse.from_message(
        message,
        conversation_provider=getattr(conversation, "provider", None),
        conversation_model=getattr(conversation, "model", None),
    )


def _sse_error_message(exc: Exception) -> str:
    if isinstance(exc, APEError):
        return exc.message
    if isinstance(exc, ProviderError):
        return "The language model provider is temporarily unavailable."
    return "An unexpected error occurred."


@router.post(
    "",
    response_model=ApiResponse[ConversationResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a conversation",
)
async def create_conversation(
    project_id: uuid.UUID,
    body: ConversationCreate,
    service: ConversationServiceDep,
) -> ApiResponse[ConversationResponse]:
    del project_id
    conversation = await service.create(body)
    return ApiResponse.ok(ConversationResponse.model_validate(conversation))


@router.get(
    "",
    response_model=ApiResponse[PaginatedResult[ConversationResponse]],
    summary="List conversations",
)
async def list_conversations(
    project_id: uuid.UUID,
    service: ConversationServiceDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    include_deleted: bool = Query(default=False),
    is_active: bool | None = Query(default=None),
) -> ApiResponse[PaginatedResult[ConversationResponse]]:
    del project_id
    params = ListParams(
        limit=limit,
        offset=offset,
        include_deleted=include_deleted,
        is_active=is_active,
    )
    page = await service.list(params)
    return ApiResponse.ok(
        PaginatedResult[ConversationResponse](
            items=[ConversationResponse.model_validate(item) for item in page.items],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )
    )


@router.get(
    "/{conversation_id}",
    response_model=ApiResponse[ConversationResponse],
    summary="Get a conversation by id",
)
async def get_conversation(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    service: ConversationServiceDep,
) -> ApiResponse[ConversationResponse]:
    del project_id
    conversation = await service.get(conversation_id)
    return ApiResponse.ok(ConversationResponse.model_validate(conversation))


@router.patch(
    "/{conversation_id}",
    response_model=ApiResponse[ConversationResponse],
    summary="Update conversation metadata",
)
async def update_conversation(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    body: ConversationUpdate,
    service: ConversationServiceDep,
) -> ApiResponse[ConversationResponse]:
    del project_id
    conversation = await service.update(conversation_id, body)
    return ApiResponse.ok(ConversationResponse.model_validate(conversation))


@router.patch(
    "/{conversation_id}/status",
    response_model=ApiResponse[ConversationResponse],
    summary="Toggle conversation active status",
)
async def toggle_conversation_status(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    service: ConversationServiceDep,
) -> ApiResponse[ConversationResponse]:
    del project_id
    conversation = await service.toggle_status(conversation_id)
    return ApiResponse.ok(ConversationResponse.model_validate(conversation))


@router.delete(
    "/{conversation_id}",
    response_model=ApiResponse[ConversationResponse],
    summary="Soft-delete a conversation",
)
async def delete_conversation(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    service: ConversationServiceDep,
) -> ApiResponse[ConversationResponse]:
    del project_id
    conversation = await service.soft_delete(conversation_id)
    return ApiResponse.ok(ConversationResponse.model_validate(conversation))


@router.get(
    "/{conversation_id}/messages",
    response_model=ApiResponse[PaginatedResult[MessageResponse]],
    summary="List messages in a conversation",
)
async def list_messages(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    service: ConversationServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[PaginatedResult[MessageResponse]]:
    del project_id
    conversation = await service.get(conversation_id)
    page = await service.list_messages(conversation_id, limit=limit, offset=offset)
    return ApiResponse.ok(
        PaginatedResult[MessageResponse](
            items=[_message_response(item, conversation) for item in page.items],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )
    )


@router.post(
    "/{conversation_id}/messages",
    response_model=ApiResponse[ChatTurnResponse],
    status_code=status.HTTP_200_OK,
    summary="Send a message and receive a grounded answer",
)
async def send_message(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    body: MessageSendRequest,
    service: ChatServiceDep,
) -> ApiResponse[ChatTurnResponse]:
    del project_id
    turn = await service.send_message(conversation_id, body)
    return ApiResponse.ok(turn)


@router.post(
    "/{conversation_id}/messages/stream",
    summary="Send a message and stream the grounded answer (SSE)",
)
async def stream_message(
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    body: MessageSendRequest,
    request: Request,
    service: ChatServiceDep,
) -> StreamingResponse:
    del project_id

    async def event_generator() -> AsyncIterator[str]:
        try:
            async for item in service.stream_message(
                conversation_id,
                body,
                should_cancel=request.is_disconnected,
            ):
                if await request.is_disconnected():
                    break
                if isinstance(item, str):
                    payload = json.dumps({"event": "token", "delta": item})
                else:
                    payload = json.dumps(item)
                yield f"data: {payload}\n\n"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not await request.is_disconnected():
                logger.warning("chat_stream_failed", error=str(exc))
                error_payload = json.dumps({"event": "error", "message": _sse_error_message(exc)})
                yield f"data: {error_payload}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
