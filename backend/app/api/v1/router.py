"""Aggregated API v1 router.

Feature routers are registered from the composition layer, e.g.::

    from app.api.v1.routes.projects_router import router as projects_router
    api_v1_router.include_router(projects_router, prefix="/projects", tags=["projects"])
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.v1.routes.conversations_router import router as conversations_router
from app.api.v1.routes.documents_router import router as documents_router
from app.api.v1.routes.jobs_router import router as jobs_router
from app.api.v1.routes.organizations_router import router as organizations_router
from app.api.v1.routes.projects_router import router as projects_router
from app.api.v1.routes.search_router import router as search_router
from app.dependencies.auth import require_organization_api_key
from app.dependencies.projects import ensure_project_accessible

api_v1_router = APIRouter()
api_v1_router.include_router(organizations_router, prefix="/organizations", tags=["organizations"])

_business_router = APIRouter(dependencies=[Depends(require_organization_api_key)])
_business_router.include_router(projects_router, prefix="/projects", tags=["projects"])

_project_nested_router = APIRouter(dependencies=[Depends(ensure_project_accessible)])
_project_nested_router.include_router(
    documents_router,
    prefix="/projects/{project_id}/documents",
    tags=["documents"],
)
_project_nested_router.include_router(
    jobs_router,
    prefix="/projects/{project_id}/jobs",
    tags=["jobs"],
)
_project_nested_router.include_router(
    search_router,
    prefix="/projects/{project_id}",
    tags=["search"],
)
_project_nested_router.include_router(
    conversations_router,
    prefix="/projects/{project_id}/conversations",
    tags=["conversations"],
)
_business_router.include_router(_project_nested_router)
api_v1_router.include_router(_business_router)
