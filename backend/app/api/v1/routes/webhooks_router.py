"""Project-scoped webhook configuration, delivery history, and replay APIs."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.core.http.envelopes import ApiResponse
from app.dependencies.webhooks import WebhookServiceDep
from app.modules.webhooks.schemas.webhook import (
    WebhookAttemptResponse,
    WebhookDeliveryDetailResponse,
    WebhookDeliveryResponse,
    WebhookEndpointCreate,
    WebhookEndpointCreatedResponse,
    WebhookEndpointResponse,
    WebhookEndpointStatusUpdate,
    WebhookEventResponse,
)
from app.platform.http.pagination import PaginatedResult
from app.platform.webhooks.contracts import WebhookDeliveryState

router = APIRouter()


@router.post(
    "/endpoints",
    response_model=ApiResponse[WebhookEndpointCreatedResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a signed webhook endpoint",
)
async def create_endpoint(
    project_id: uuid.UUID,
    request: WebhookEndpointCreate,
    service: WebhookServiceDep,
) -> ApiResponse[WebhookEndpointCreatedResponse]:
    del project_id
    endpoint, secret = await service.create_endpoint(request)
    return ApiResponse.ok(
        WebhookEndpointCreatedResponse.model_validate(
            {
                **WebhookEndpointResponse.model_validate(endpoint).model_dump(),
                "signing_secret": secret,
            }
        ),
        message="Webhook endpoint created. Store the signing secret securely.",
    )


@router.get(
    "/endpoints",
    response_model=ApiResponse[PaginatedResult[WebhookEndpointResponse]],
    summary="List webhook endpoints",
)
async def list_endpoints(
    project_id: uuid.UUID,
    service: WebhookServiceDep,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[PaginatedResult[WebhookEndpointResponse]]:
    del project_id
    page = await service.list_endpoints(limit=limit, offset=offset)
    return ApiResponse.ok(
        PaginatedResult[WebhookEndpointResponse](
            items=[WebhookEndpointResponse.model_validate(item) for item in page.items],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )
    )


@router.patch(
    "/endpoints/{endpoint_id}/status",
    response_model=ApiResponse[WebhookEndpointResponse],
    summary="Enable or disable a webhook endpoint",
)
async def update_endpoint_status(
    project_id: uuid.UUID,
    endpoint_id: uuid.UUID,
    request: WebhookEndpointStatusUpdate,
    service: WebhookServiceDep,
) -> ApiResponse[WebhookEndpointResponse]:
    del project_id
    endpoint = await service.set_endpoint_status(endpoint_id, request)
    return ApiResponse.ok(WebhookEndpointResponse.model_validate(endpoint))


@router.get(
    "/deliveries",
    response_model=ApiResponse[PaginatedResult[WebhookDeliveryResponse]],
    summary="List webhook delivery history",
)
async def list_deliveries(
    project_id: uuid.UUID,
    service: WebhookServiceDep,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    endpoint_id: uuid.UUID | None = Query(default=None),
    state: WebhookDeliveryState | None = Query(default=None),
) -> ApiResponse[PaginatedResult[WebhookDeliveryResponse]]:
    del project_id
    page = await service.list_deliveries(
        limit=limit,
        offset=offset,
        endpoint_id=endpoint_id,
        state=state,
    )
    return ApiResponse.ok(
        PaginatedResult[WebhookDeliveryResponse](
            items=[WebhookDeliveryResponse.model_validate(item) for item in page.items],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )
    )


@router.get(
    "/deliveries/{delivery_id}",
    response_model=ApiResponse[WebhookDeliveryDetailResponse],
    summary="Inspect a webhook delivery and every HTTP attempt",
)
async def get_delivery(
    project_id: uuid.UUID,
    delivery_id: uuid.UUID,
    service: WebhookServiceDep,
) -> ApiResponse[WebhookDeliveryDetailResponse]:
    del project_id
    delivery, event, attempts = await service.get_delivery(delivery_id)
    return ApiResponse.ok(
        WebhookDeliveryDetailResponse.model_validate(
            {
                **WebhookDeliveryResponse.model_validate(delivery).model_dump(),
                "event": WebhookEventResponse.model_validate(event),
                "attempts": [WebhookAttemptResponse.model_validate(item) for item in attempts],
            }
        )
    )


@router.post(
    "/deliveries/{delivery_id}/replay",
    response_model=ApiResponse[WebhookDeliveryResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Replay an existing event with the same event ID",
)
async def replay_delivery(
    project_id: uuid.UUID,
    delivery_id: uuid.UUID,
    service: WebhookServiceDep,
) -> ApiResponse[WebhookDeliveryResponse]:
    del project_id
    return ApiResponse.ok(
        WebhookDeliveryResponse.model_validate(await service.replay(delivery_id)),
        message="Webhook replay queued.",
    )
