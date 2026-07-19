"""Project-scoped webhook endpoint, event, and delivery persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select

from app.models.webhook_endpoint import WebhookEndpoint
from app.models.webhook_event import WebhookDelivery, WebhookDeliveryAttempt, WebhookEvent
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository
from app.platform.webhooks.contracts import WebhookDeliveryState


class WebhookEndpointRepository(ProjectScopedRepository[WebhookEndpoint]):
    model = WebhookEndpoint

    async def list_recent(self, *, limit: int, offset: int) -> list[WebhookEndpoint]:
        result = await self._session.execute(
            self._scoped()
            .order_by(WebhookEndpoint.created_at.desc(), WebhookEndpoint.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def count_all(self) -> int:
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(WebhookEndpoint)
                .where(WebhookEndpoint.project_id == self._project_id)
            )
            or 0
        )

    async def list_subscribed(self, event_type: str) -> list[WebhookEndpoint]:
        result = await self._session.execute(
            self._scoped().where(
                WebhookEndpoint.is_enabled.is_(True),
                WebhookEndpoint.event_types.contains([event_type]),
            )
        )
        return list(result.scalars().all())


class WebhookEventRepository(ProjectScopedRepository[WebhookEvent]):
    model = WebhookEvent

    async def get_by_source_key(self, source_key: str) -> WebhookEvent | None:
        return await self._session.scalar(
            self._scoped().where(WebhookEvent.source_key == source_key)
        )


class WebhookDeliveryRepository(ProjectScopedRepository[WebhookDelivery]):
    model = WebhookDelivery

    async def list_recent(
        self,
        *,
        limit: int,
        offset: int,
        endpoint_id: uuid.UUID | None = None,
        state: WebhookDeliveryState | None = None,
    ) -> list[WebhookDelivery]:
        stmt = self._scoped()
        if endpoint_id is not None:
            stmt = stmt.where(WebhookDelivery.endpoint_id == endpoint_id)
        if state is not None:
            stmt = stmt.where(WebhookDelivery.state == state)
        result = await self._session.execute(
            stmt.order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def count_filtered(
        self,
        *,
        endpoint_id: uuid.UUID | None = None,
        state: WebhookDeliveryState | None = None,
    ) -> int:
        clauses = [WebhookDelivery.project_id == self._project_id]
        if endpoint_id is not None:
            clauses.append(WebhookDelivery.endpoint_id == endpoint_id)
        if state is not None:
            clauses.append(WebhookDelivery.state == state)
        return int(
            await self._session.scalar(
                select(func.count()).select_from(WebhookDelivery).where(*clauses)
            )
            or 0
        )

    async def claim_next(
        self, *, worker_id: str, lease_seconds: int
    ) -> WebhookDelivery | None:
        now = datetime.now(UTC)
        delivery = await self._session.scalar(
            select(WebhookDelivery)
            .join(WebhookEndpoint, WebhookEndpoint.id == WebhookDelivery.endpoint_id)
            .where(
                WebhookDelivery.project_id == self._project_id,
                WebhookEndpoint.project_id == self._project_id,
                WebhookEndpoint.is_enabled.is_(True),
                or_(
                    and_(
                        WebhookDelivery.state.in_(
                            [
                                WebhookDeliveryState.PENDING,
                                WebhookDeliveryState.RETRY_SCHEDULED,
                            ]
                        ),
                        WebhookDelivery.available_at <= now,
                    ),
                    and_(
                        WebhookDelivery.state == WebhookDeliveryState.DELIVERING,
                        WebhookDelivery.lease_expires_at.is_not(None),
                        WebhookDelivery.lease_expires_at < now,
                    ),
                ),
                WebhookDelivery.attempt_count < WebhookDelivery.max_attempts,
            )
            .order_by(WebhookDelivery.available_at, WebhookDelivery.created_at, WebhookDelivery.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if delivery is None:
            return None
        delivery.state = WebhookDeliveryState.DELIVERING
        delivery.lease_owner = worker_id
        delivery.lease_expires_at = now + timedelta(seconds=lease_seconds)
        delivery.attempt_count += 1
        return delivery

    async def lock_owned(
        self, delivery_id: uuid.UUID, *, worker_id: str
    ) -> WebhookDelivery | None:
        return await self._session.scalar(
            self._scoped()
            .where(
                WebhookDelivery.id == delivery_id,
                WebhookDelivery.state == WebhookDeliveryState.DELIVERING,
                WebhookDelivery.lease_owner == worker_id,
            )
            .with_for_update()
        )

    async def next_replay_number(self, endpoint_id: uuid.UUID, event_id: uuid.UUID) -> int:
        value = await self._session.scalar(
            select(func.max(WebhookDelivery.replay_number)).where(
                WebhookDelivery.project_id == self._project_id,
                WebhookDelivery.endpoint_id == endpoint_id,
                WebhookDelivery.event_id == event_id,
            )
        )
        return int(value or 0) + 1


class WebhookAttemptRepository(ProjectScopedRepository[WebhookDeliveryAttempt]):
    model = WebhookDeliveryAttempt

    async def list_for_delivery(self, delivery_id: uuid.UUID) -> list[WebhookDeliveryAttempt]:
        result = await self._session.execute(
            self._scoped()
            .where(WebhookDeliveryAttempt.delivery_id == delivery_id)
            .order_by(WebhookDeliveryAttempt.attempt_number, WebhookDeliveryAttempt.id)
        )
        return list(result.scalars().all())
