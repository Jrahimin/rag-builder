"""Standard API response envelopes — success and failure contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ResponseMeta(BaseModel):
    """Lightweight metadata attached to every response for traceability."""

    request_id: str | None = None
    trace_id: str | None = None


class ApiResponse[T](BaseModel):
    """Standard success envelope wrapping a typed ``data`` payload."""

    success: bool = True
    message: str | None = None
    data: T | None = None
    meta: ResponseMeta | None = None

    @classmethod
    def ok(
        cls,
        data: T | None = None,
        *,
        message: str | None = None,
        meta: ResponseMeta | None = None,
    ) -> ApiResponse[T]:
        """Convenience constructor for a successful response."""
        return cls(success=True, data=data, message=message, meta=meta)


class ErrorDetail(BaseModel):
    """A single, granular error (e.g. one field validation failure)."""

    message: str
    field: str | None = None
    type: str | None = None


class ErrorInfo(BaseModel):
    """Machine- and human-readable error description."""

    code: str
    message: str
    request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Standard failure envelope returned by all exception handlers."""

    error: ErrorInfo
