"""Attach project/workload context around HTTP, durable jobs and explicit CLI work."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import Settings
from app.platform.infra.providers.provider_work_repository import ProviderWorkRepository
from app.platform.providers.provider_work import (
    ProviderWorkScope,
    attached_provider_scope,
    current_provider_scope,
)


@contextmanager
def provider_work_scope(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
    reference: str,
    workload: str,
) -> Iterator[ProviderWorkScope]:
    parent = current_provider_scope()
    if parent is not None and parent.workload == "evaluation" and parent.project_id == project_id:
        reference, workload = parent.reference, parent.workload
        settings = settings.model_copy(update={"provider_costs": parent.config})
    scope = ProviderWorkScope(
        project_id=project_id,
        reference=reference,
        workload=workload,
        environment=settings.app.env.value,
        embedding_set_version=settings.retrieval.embedding_set_version,
        config=settings.provider_costs,
        store=ProviderWorkRepository(sessions),
    )
    with attached_provider_scope(scope):
        yield scope


class ProviderWorkMiddleware:
    """Pure ASGI scope remains attached through SSE body delivery and cancellation."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "").split("/")
        try:
            project_id = uuid.UUID(path[path.index("projects") + 1])
        except (ValueError, IndexError):
            await self.app(scope, receive, send)
            return
        app = scope.get("app")
        if scope["type"] != "http" or app is None or not hasattr(app.state, "db"):
            await self.app(scope, receive, send)
            return
        workload = (
            "evaluation" if "evaluations" in path else "chat" if "messages" in path else "retrieval"
        )
        with provider_work_scope(
            app.state.settings,
            app.state.db.session_factory,
            project_id,
            str(uuid.uuid4()),
            workload,
        ):
            await self.app(scope, receive, send)
