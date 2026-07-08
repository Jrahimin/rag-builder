"""Domain events that change organization API key authentication validity."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OrganizationAuthInvalidated:
    """Organization auth state changed; drop cached credentials for the tenant."""

    organization_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ApiKeyAuthInvalidated:
    """An API key is no longer valid; drop its cached verification entry."""

    key_hash: str


AuthDomainEvent = OrganizationAuthInvalidated | ApiKeyAuthInvalidated
