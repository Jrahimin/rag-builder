"""Authenticated request context for organization API key auth."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

DEFAULT_ORGANIZATION_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


@dataclass(frozen=True, slots=True)
class AuthenticatedOrganization:
    """Resolved organization identity after successful API key verification."""

    organization_id: uuid.UUID | None
    api_key_id: uuid.UUID | None
    organization_is_active: bool = True

    @property
    def is_auth_bypassed(self) -> bool:
        """True when auth is disabled and no organization was resolved."""
        return self.organization_id is None
