"""Rate limiting infrastructure contracts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    """Outcome of a rate-limit check."""

    allowed: bool
    retry_after_seconds: int = 0


class RateLimiter(Protocol):
    """Organization-scoped request rate limiter."""

    async def check(self, organization_id: uuid.UUID) -> RateLimitResult: ...
