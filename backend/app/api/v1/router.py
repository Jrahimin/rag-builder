"""Aggregated API v1 router.

Feature routers are registered from the composition layer, e.g.::

    from app.api.v1.routes.projects import router as projects_router
    api_v1_router.include_router(projects_router, prefix="/projects", tags=["projects"])
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes.projects import router as projects_router

api_v1_router = APIRouter()
api_v1_router.include_router(projects_router, prefix="/projects", tags=["projects"])
