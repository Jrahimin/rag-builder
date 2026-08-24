"""Persistence for platform operator accounts."""

from __future__ import annotations

from app.models.admin_user import AdminUser
from app.platform.persistence.async_repository import AsyncRepository


class AdminUserRepository(AsyncRepository[AdminUser]):
    """Lifecycle CRUD for the AdminUser aggregate."""

    model = AdminUser
