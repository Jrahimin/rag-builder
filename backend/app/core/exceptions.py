"""Application-level exception hierarchy.

Services and providers raise these domain exceptions instead of leaking
framework- or vendor-specific errors. Each maps to an HTTP status code and a
stable, machine-readable ``code`` so clients can branch on failures without
parsing human-readable messages.
"""

from __future__ import annotations

from typing import Any

from app.core.http.envelopes import ErrorDetail


class APEError(Exception):
    """Base class for all expected application errors.

    Attributes:
        status_code: HTTP status returned to the client.
        code: Stable, machine-readable error identifier.
        message: Human-readable description (safe to expose to clients).
        details: Optional granular error breakdown.
    """

    status_code: int = 500
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: list[ErrorDetail] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details
        # Structured context for logs (never serialized to the client).
        self.context = context or {}
        super().__init__(self.message)


class BadRequestError(APEError):
    status_code = 400
    code = "bad_request"
    message = "The request was invalid."


class UnauthorizedError(APEError):
    status_code = 401
    code = "unauthorized"
    message = "Authentication is required."


class ForbiddenError(APEError):
    status_code = 403
    code = "forbidden"
    message = "You do not have permission to perform this action."


class NotFoundError(APEError):
    status_code = 404
    code = "not_found"
    message = "The requested resource was not found."


class ConflictError(APEError):
    status_code = 409
    code = "conflict"
    message = "The request conflicts with the current state of the resource."


class PayloadTooLargeError(APEError):
    status_code = 413
    code = "payload_too_large"
    message = "The request payload exceeds the allowed size."


class ValidationError(APEError):
    status_code = 422
    code = "validation_error"
    message = "The request failed validation."


class RateLimitError(APEError):
    status_code = 429
    code = "rate_limited"
    message = "Rate limit exceeded."

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_seconds: int,
        code: str | None = None,
        details: list[ErrorDetail] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            message,
            code=code,
            status_code=429,
            details=details,
            context=context,
        )


class ServiceUnavailableError(APEError):
    status_code = 503
    code = "service_unavailable"
    message = "A required downstream service is unavailable."
