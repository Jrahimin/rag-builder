"""Shared pagination contracts for list endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PaginationParams(BaseModel):
    """Offset-based pagination query parameters."""

    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ListParams(PaginationParams):
    """Standard list query parameters for lifecycle-aware entities."""

    include_deleted: bool = False
    is_active: bool | None = None


class PaginatedResult[T](BaseModel):
    """A page of items with total count for deterministic list responses."""

    items: list[T]
    total: int
    limit: int
    offset: int
