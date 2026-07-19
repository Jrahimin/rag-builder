"""Webhook configuration, inspection, replay, and leased delivery orchestration."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Environment, WebhooksConfig
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.webhook_endpoint import WebhookEndpoint
from app.models.webhook_event import WebhookDelivery, WebhookDeliveryAttempt, WebhookEvent
from app.modules.webhooks.repositories.webhook_repository import (
    WebhookAttemptRepository,
    WebhookDeliveryRepository,
    WebhookEndpointRepository,
    WebhookEventRepository,
)
from app.modules.webhooks.schemas.webhook import WebhookEndpointCreate, WebhookEndpointStatusUpdate
from app.platform.audit.contracts import AuditActorType, AuditEventType, AuditOutcome, AuditRecorder
from app.platform.http.pagination import PaginatedResult
from app.platform.webhooks.contracts import WebhookDeliveryState, WebhookTransport
from app.platform.webhooks.signing import (
    canonical_webhook_body,
    derive_endpoint_secret,
    sign_webhook_payload,
)


class WebhookService:
    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        config: WebhooksConfig,
        *,
        environment: Environment,
        audit: AuditRecorder,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._config = config
        self._environment = environment
        self._audit = audit
        self._endpoints = WebhookEndpointRepository(session, project_id)
        self._events = WebhookEventRepository(session, project_id)
        self._deliveries = WebhookDeliveryRepository(session, project_id)
        self._attempts = WebhookAttemptRepository(session, project_id)

    async def create_endpoint(self, request: WebhookEndpointCreate) -> tuple[WebhookEndpoint, str]:
        if not self._config.enabled:
            raise BadRequestError(message="Webhooks are disabled.", code="webhooks_disabled")
        url = str(request.url)
        if self._environment is Environment.PRODUCTION and request.url.scheme != "https":
            raise BadRequestError(
                message="Production webhook endpoints must use HTTPS.",
                code="webhook_https_required",
            )
        endpoint = WebhookEndpoint(
            project_id=self._project_id,
            url=url,
            description=request.description,
            event_types=[event.value for event in request.event_types],
        )
        self._endpoints.add(endpoint)
        await self._endpoints.flush()
        self._audit.record(
            event_type=AuditEventType.WEBHOOK_ENDPOINT_CREATED,
            actor_type=AuditActorType.OPERATOR,
            resource_type="webhook_endpoint",
            resource_id=endpoint.id,
            outcome=AuditOutcome.SUCCESS,
            detail={"event_types": endpoint.event_types},
        )
        await self._session.commit()
        await self._session.refresh(endpoint)
        return endpoint, derive_endpoint_secret(self._config.signing_key, endpoint.id)

    async def list_endpoints(self, *, limit: int, offset: int) -> PaginatedResult[WebhookEndpoint]:
        return PaginatedResult(
            items=await self._endpoints.list_recent(limit=limit, offset=offset),
            total=await self._endpoints.count_all(),
            limit=limit,
            offset=offset,
        )

    async def get_endpoint(self, endpoint_id: uuid.UUID) -> WebhookEndpoint:
        endpoint = await self._endpoints.get_by_id(endpoint_id)
        if endpoint is None:
            raise NotFoundError(
                message="Webhook endpoint not found.", code="webhook_endpoint_not_found"
            )
        return endpoint

    async def set_endpoint_status(
        self, endpoint_id: uuid.UUID, request: WebhookEndpointStatusUpdate
    ) -> WebhookEndpoint:
        endpoint = await self.get_endpoint(endpoint_id)
        endpoint.is_enabled = request.enabled
        endpoint.disabled_at = None if request.enabled else datetime.now(UTC)
        endpoint.disabled_reason = (
            None if request.enabled else (request.reason or "operator_disabled")
        )
        self._audit.record(
            event_type=(
                AuditEventType.WEBHOOK_ENDPOINT_ENABLED
                if request.enabled
                else AuditEventType.WEBHOOK_ENDPOINT_DISABLED
            ),
            actor_type=AuditActorType.OPERATOR,
            resource_type="webhook_endpoint",
            resource_id=endpoint.id,
            outcome=AuditOutcome.SUCCESS,
            detail={"reason": endpoint.disabled_reason},
        )
        await self._session.commit()
        await self._session.refresh(endpoint)
        return endpoint

    async def list_deliveries(
        self,
        *,
        limit: int,
        offset: int,
        endpoint_id: uuid.UUID | None,
        state: WebhookDeliveryState | None,
    ) -> PaginatedResult[WebhookDelivery]:
        return PaginatedResult(
            items=await self._deliveries.list_recent(
                limit=limit, offset=offset, endpoint_id=endpoint_id, state=state
            ),
            total=await self._deliveries.count_filtered(endpoint_id=endpoint_id, state=state),
            limit=limit,
            offset=offset,
        )

    async def get_delivery(
        self, delivery_id: uuid.UUID
    ) -> tuple[WebhookDelivery, WebhookEvent, list[WebhookDeliveryAttempt]]:
        delivery = await self._deliveries.get_by_id(delivery_id)
        if delivery is None:
            raise NotFoundError(
                message="Webhook delivery not found.", code="webhook_delivery_not_found"
            )
        event = await self._events.get_by_id(delivery.event_id)
        if event is None:  # pragma: no cover - protected by FK
            raise RuntimeError("Webhook event is missing")
        return delivery, event, await self._attempts.list_for_delivery(delivery.id)

    async def replay(self, delivery_id: uuid.UUID) -> WebhookDelivery:
        original, _event, _attempts = await self.get_delivery(delivery_id)
        endpoint = await self.get_endpoint(original.endpoint_id)
        if not endpoint.is_enabled:
            raise BadRequestError(
                message="Enable the webhook endpoint before replaying a delivery.",
                code="webhook_endpoint_disabled",
            )
        replay = WebhookDelivery(
            project_id=self._project_id,
            endpoint_id=original.endpoint_id,
            event_id=original.event_id,
            replay_of_delivery_id=original.id,
            replay_number=await self._deliveries.next_replay_number(
                original.endpoint_id, original.event_id
            ),
            max_attempts=self._config.max_attempts,
        )
        self._deliveries.add(replay)
        await self._deliveries.flush()
        self._audit.record(
            event_type=AuditEventType.WEBHOOK_DELIVERY_REPLAYED,
            actor_type=AuditActorType.OPERATOR,
            resource_type="webhook_delivery",
            resource_id=replay.id,
            outcome=AuditOutcome.SUCCESS,
            detail={"replay_of_delivery_id": str(original.id), "event_id": str(original.event_id)},
        )
        await self._session.commit()
        await self._session.refresh(replay)
        return replay


class WebhookDeliveryService:
    """One-at-a-time delivery worker used by the application dispatcher."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        config: WebhooksConfig,
        transport: WebhookTransport,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._config = config
        self._transport = transport
        self._endpoints = WebhookEndpointRepository(session, project_id)
        self._events = WebhookEventRepository(session, project_id)
        self._deliveries = WebhookDeliveryRepository(session, project_id)
        self._attempts = WebhookAttemptRepository(session, project_id)

    async def deliver_next(self, *, worker_id: str) -> bool:
        delivery = await self._deliveries.claim_next(
            worker_id=worker_id,
            lease_seconds=self._config.delivery_lease_seconds,
        )
        if delivery is None:
            await self._session.rollback()
            return False
        await self._session.commit()
        endpoint = await self._endpoints.get_by_id(delivery.endpoint_id)
        event = await self._events.get_by_id(delivery.event_id)
        if endpoint is None or event is None:  # pragma: no cover - protected by FKs
            raise RuntimeError("Webhook delivery references are missing")

        payload = {
            "id": str(event.id),
            "type": event.event_type.value,
            "api_version": event.api_version,
            "occurred_at": event.occurred_at.isoformat(),
            "project_id": str(event.project_id),
            "data": event.data,
        }
        body = canonical_webhook_body(payload)
        timestamp = str(int(datetime.now(UTC).timestamp()))
        signature = sign_webhook_payload(
            secret=derive_endpoint_secret(self._config.signing_key, endpoint.id),
            timestamp=timestamp,
            event_id=event.id,
            body=body,
        )
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "APE-Webhooks/1.0",
            "X-APE-Event-ID": str(event.id),
            "X-APE-Event-Type": event.event_type.value,
            "X-APE-Timestamp": timestamp,
            "X-APE-Signature": f"v1={signature}",
            "Idempotency-Key": str(event.id),
        }
        response_status: int | None = None
        response_body = ""
        latency_ms: float | None = None
        error: str | None = None
        try:
            response = await self._transport.post(
                url=endpoint.url,
                body=body,
                headers=headers,
                timeout_seconds=self._config.delivery_timeout_seconds,
            )
            response_status = response.status_code
            response_body = response.body
            latency_ms = response.latency_ms
            if not 200 <= response.status_code < 300:
                error = f"receiver returned HTTP {response.status_code}"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        await self._finish(
            delivery.id,
            worker_id=worker_id,
            status_code=response_status,
            response_body=response_body,
            latency_ms=latency_ms,
            error=error,
        )
        return True

    async def _finish(
        self,
        delivery_id: uuid.UUID,
        *,
        worker_id: str,
        status_code: int | None,
        response_body: str,
        latency_ms: float | None,
        error: str | None,
    ) -> None:
        delivery = await self._deliveries.lock_owned(delivery_id, worker_id=worker_id)
        if delivery is None:
            return
        now = datetime.now(UTC)
        attempt = WebhookDeliveryAttempt(
            project_id=self._project_id,
            delivery_id=delivery.id,
            attempt_number=delivery.attempt_count,
            attempted_at=now,
            status_code=status_code,
            latency_ms=latency_ms,
            error=error,
            response_excerpt=response_body[: self._config.response_excerpt_chars] or None,
        )
        self._attempts.add(attempt)
        delivery.last_status_code = status_code
        delivery.last_error = error
        delivery.lease_owner = None
        delivery.lease_expires_at = None
        if error is None:
            delivery.state = WebhookDeliveryState.SUCCEEDED
            delivery.delivered_at = now
        elif delivery.attempt_count >= delivery.max_attempts:
            delivery.state = WebhookDeliveryState.FAILED
        else:
            delivery.state = WebhookDeliveryState.RETRY_SCHEDULED
            delay = min(
                self._config.retry_base_seconds * (2 ** max(delivery.attempt_count - 1, 0)),
                self._config.retry_max_seconds,
            )
            delivery.available_at = now + timedelta(seconds=delay)
        await self._session.commit()
