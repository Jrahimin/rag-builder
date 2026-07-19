"""Integration coverage for durable signed delivery, retry, history, and replay."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.composition.webhooks import DatabaseWebhookEventPublisher
from app.core.config import get_settings
from app.models.webhook_event import WebhookDelivery, WebhookEvent
from app.modules.webhooks.services.webhook_service import WebhookDeliveryService
from app.platform.providers.contracts.webhook import WebhookHttpResponse, WebhookTransport
from app.platform.webhooks.contracts import WebhookEventDefinition
from app.platform.webhooks.signing import verify_webhook_signature
from tests.integration.knowledge_helpers import run_captured_document_jobs

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class _SequenceTransport(WebhookTransport):
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, dict[str, str]]] = []

    async def post(
        self,
        *,
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> WebhookHttpResponse:
        del url, timeout_seconds
        self.calls.append((body, headers))
        if len(self.calls) == 1:
            return WebhookHttpResponse(status_code=503, body="temporarily down", latency_ms=4.0)
        return WebhookHttpResponse(status_code=204, body="", latency_ms=2.0)


class _AlwaysFailTransport(WebhookTransport):
    async def post(
        self,
        *,
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> WebhookHttpResponse:
        del url, body, headers, timeout_seconds
        return WebhookHttpResponse(status_code=503, body="still unavailable", latency_ms=3.0)


async def test_document_outcome_delivery_retries_is_signed_and_replays_same_event(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list,
) -> None:
    project = await db_client.post(
        "/api/v1/projects", json={"name": f"Webhooks {uuid.uuid4().hex[:8]}"}
    )
    project_id = project.json()["data"]["id"]
    endpoint_response = await db_client.post(
        f"/api/v1/projects/{project_id}/webhooks/endpoints",
        json={
            "url": "https://customer.example.test/ape",
            "event_types": ["document.processing.succeeded.v1"],
        },
    )
    assert endpoint_response.status_code == 201
    endpoint = endpoint_response.json()["data"]
    signing_secret = endpoint["signing_secret"]

    upload = await db_client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("webhook.txt", b"hosted webhook integration", "text/plain")},
    )
    assert upload.status_code == 201
    await run_captured_document_jobs(integration_connection, captured_jobs)

    deliveries = await db_client.get(f"/api/v1/projects/{project_id}/webhooks/deliveries")
    assert deliveries.status_code == 200
    delivery = deliveries.json()["data"]["items"][0]
    assert delivery["state"] == "pending"

    transport = _SequenceTransport()
    settings = get_settings()
    project_uuid = uuid.UUID(project_id)
    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        service = WebhookDeliveryService(session, project_uuid, settings.webhooks, transport)
        assert await service.deliver_next(worker_id="integration-webhook")
        persisted = await session.get(WebhookDelivery, uuid.UUID(delivery["id"]))
        assert persisted is not None
        assert persisted.state.value == "retry_scheduled"
        persisted.available_at = datetime.now(UTC)
        await session.commit()
        assert await service.deliver_next(worker_id="integration-webhook")

    body, headers = transport.calls[-1]
    body_payload = json.loads(body)
    event_id = uuid.UUID(headers["X-APE-Event-ID"])
    assert body_payload["id"] == str(event_id)
    assert headers["Idempotency-Key"] == str(event_id)
    assert verify_webhook_signature(
        secret=signing_secret,
        timestamp=headers["X-APE-Timestamp"],
        event_id=event_id,
        body=body,
        signature=headers["X-APE-Signature"],
    )

    detail = await db_client.get(
        f"/api/v1/projects/{project_id}/webhooks/deliveries/{delivery['id']}"
    )
    assert detail.status_code == 200
    detail_data = detail.json()["data"]
    assert detail_data["state"] == "succeeded"
    assert [attempt["status_code"] for attempt in detail_data["attempts"]] == [503, 204]

    replay = await db_client.post(
        f"/api/v1/projects/{project_id}/webhooks/deliveries/{delivery['id']}/replay"
    )
    assert replay.status_code == 202
    replay_data = replay.json()["data"]
    assert replay_data["event_id"] == str(event_id)
    assert replay_data["replay_of_delivery_id"] == delivery["id"]

    async with AsyncSession(
        bind=integration_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        event = await session.scalar(
            select(WebhookEvent).where(WebhookEvent.project_id == project_uuid)
        )
        assert event is not None
        publisher = DatabaseWebhookEventPublisher(session, project_uuid, settings)
        duplicate = WebhookEventDefinition(
            event_type=event.event_type,
            source_key=event.source_key,
            source_type=event.source_type,
            source_id=event.source_id,
            data=event.data,
            occurred_at=event.occurred_at,
        )
        await publisher.stage(duplicate)
        await publisher.stage(duplicate)
        await session.commit()
        event_count = len(
            list(
                (
                    await session.execute(
                        select(WebhookEvent).where(WebhookEvent.project_id == project_uuid)
                    )
                ).scalars()
            )
        )
        assert event_count == 1

        replay_model = await session.get(WebhookDelivery, uuid.UUID(replay_data["id"]))
        assert replay_model is not None
        replay_model.max_attempts = 2
        await session.commit()
        failing = WebhookDeliveryService(
            session, project_uuid, settings.webhooks, _AlwaysFailTransport()
        )
        assert await failing.deliver_next(worker_id="integration-webhook-failure")
        replay_model = await session.get(WebhookDelivery, replay_model.id)
        assert replay_model is not None
        replay_model.available_at = datetime.now(UTC)
        await session.commit()
        assert await failing.deliver_next(worker_id="integration-webhook-failure")

    failed_detail = await db_client.get(
        f"/api/v1/projects/{project_id}/webhooks/deliveries/{replay_data['id']}"
    )
    assert failed_detail.status_code == 200
    assert failed_detail.json()["data"]["state"] == "failed"
    assert [item["status_code"] for item in failed_detail.json()["data"]["attempts"]] == [
        503,
        503,
    ]


async def test_disabled_endpoint_blocks_replay(
    db_client: AsyncClient,
    integration_connection: AsyncConnection,
    captured_jobs: list,
) -> None:
    project = await db_client.post(
        "/api/v1/projects", json={"name": f"Disabled hook {uuid.uuid4().hex[:8]}"}
    )
    project_id = project.json()["data"]["id"]
    endpoint = await db_client.post(
        f"/api/v1/projects/{project_id}/webhooks/endpoints",
        json={
            "url": "https://customer.example.test/disabled",
            "event_types": ["document.processing.succeeded.v1"],
        },
    )
    endpoint_id = endpoint.json()["data"]["id"]
    upload = await db_client.post(
        f"/api/v1/projects/{project_id}/documents",
        files={"file": ("disabled.txt", b"disable and retain history", "text/plain")},
    )
    assert upload.status_code == 201
    await run_captured_document_jobs(integration_connection, captured_jobs)
    deliveries = await db_client.get(f"/api/v1/projects/{project_id}/webhooks/deliveries")
    delivery_id = deliveries.json()["data"]["items"][0]["id"]
    disabled = await db_client.patch(
        f"/api/v1/projects/{project_id}/webhooks/endpoints/{endpoint_id}/status",
        json={"enabled": False, "reason": "receiver retired"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["data"]["is_enabled"] is False
    replay = await db_client.post(
        f"/api/v1/projects/{project_id}/webhooks/deliveries/{delivery_id}/replay"
    )
    assert replay.status_code == 400
    assert replay.json()["error"]["code"] == "webhook_endpoint_disabled"
