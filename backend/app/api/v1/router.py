"""Aggregated API v1 router.

Feature routers (projects, documents, connectors, retrieval, chat, ...) will
be registered here in later sprints, e.g.::

    from app.modules.projects.api.project_router import router as projects_router
    api_v1_router.include_router(projects_router)

It is intentionally empty for the foundation sprint - no business endpoints
exist yet.
"""

from __future__ import annotations

from fastapi import APIRouter

api_v1_router = APIRouter()
