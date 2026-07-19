"""Database event publisher and leased outbound webhook dispatcher."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.project import Project
from app.models.webhook_event import WebhookDelivery, WebhookEvent
from app.modules.webhooks.repositories.webhook_repository import WebhookEndpointRepository
from app.modules.webhooks.services.webhook_service import WebhookDeliveryService
from app.platform.providers.implementations.http_webhook_provider import HttpWebhookProvider
from app.platform.webhooks.contracts import WebhookEventDefinition, WebhookEventPublisher

logger = structlog.get_logger(__name__)


class DatabaseWebhookEventPublisher(WebhookEventPublisher):
    """Stage one immutable event and initial deliveries in the current transaction."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        settings: Settings,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._settings = settings
        self._endpoints = WebhookEndpointRepository(session, project_id)

    async def stage(self, definition: WebhookEventDefinition) -> None:
        if not self._settings.webhooks.enabled:
            return
        existing = await self._session.scalar(
            select(WebhookEvent.id).where(
                WebhookEvent.project_id == self._project_id,
                WebhookEvent.source_key == definition.source_key,
            )
        )
        if existing is not None:
            return
        endpoints = await self._endpoints.list_subscribed(definition.event_type.value)
        if not endpoints:
            return
        event = WebhookEvent(
            project_id=self._project_id,
            event_type=definition.event_type,
            source_key=definition.source_key,
            source_type=definition.source_type,
            source_id=definition.source_id,
            data=definition.data,
            occurred_at=definition.occurred_at,
        )
        self._session.add(event)
        await self._session.flush()
        self._session.add_all(
            [
                WebhookDelivery(
                    project_id=self._project_id,
                    endpoint_id=endpoint.id,
                    event_id=event.id,
                    max_attempts=self._settings.webhooks.max_attempts,
                )
                for endpoint in endpoints
            ]
        )


class WebhookDispatcher:
    """Poll project-scoped deliveries; HTTP attempts happen outside claim transactions."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._transport = HttpWebhookProvider()
        self._stop = asyncio.Event()
        self._worker_id = f"{socket.gethostname()}:{uuid.uuid4()}"

    async def run_forever(self) -> None:
        logger.info("webhook_dispatcher_started")
        try:
            while not self._stop.is_set():
                try:
                    delivered = await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("webhook_dispatcher_iteration_failed")
                    delivered = 0
                if delivered == 0:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(
                            self._stop.wait(),
                            timeout=self._settings.webhooks.dispatcher_poll_seconds,
                        )
        finally:
            logger.info("webhook_dispatcher_stopped")

    async def run_once(self) -> int:
        delivered = 0
        for project_id in await self._list_project_ids():
            async with self._session_factory() as session:
                service = WebhookDeliveryService(
                    session,
                    project_id,
                    self._settings.webhooks,
                    self._transport,
                )
                for _ in range(self._settings.webhooks.dispatcher_batch_size):
                    if not await service.deliver_next(worker_id=self._worker_id):
                        break
                    delivered += 1
        return delivered

    async def _list_project_ids(self) -> list[uuid.UUID]:
        async with self._session_factory() as session:
            result = await session.execute(select(Project.id).order_by(Project.id))
            return list(result.scalars().all())

    def stop(self) -> None:
        self._stop.set()


async def stop_webhook_dispatcher(
    dispatcher: WebhookDispatcher | None, task: asyncio.Task[None] | None
) -> None:
    if dispatcher is not None:
        dispatcher.stop()
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
