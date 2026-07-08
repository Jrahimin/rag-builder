"""Organization persistence."""

from __future__ import annotations

from app.models.organization import Organization
from app.platform.persistence.async_repository import AsyncRepository


class OrganizationRepository(AsyncRepository[Organization]):
    """Async CRUD for the Organization aggregate root."""

    model = Organization
