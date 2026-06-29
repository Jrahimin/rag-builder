"""Provider error taxonomy — separate from HTTP/application errors."""

from __future__ import annotations

from typing import Any


class ProviderError(Exception):
    """Base class for provider-layer failures."""

    code: str = "provider_error"

    def __init__(
        self,
        message: str,
        *,
        provider_name: str | None = None,
        retryable: bool = False,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.provider_name = provider_name
        self.retryable = retryable
        self.context = context or {}
        super().__init__(message)


class ProviderConnectionError(ProviderError):
    """Cannot reach the provider endpoint."""

    code = "provider_connection_error"

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, retryable=True, **kwargs)


class ProviderAuthenticationError(ProviderError):
    """Invalid or missing credentials."""

    code = "provider_authentication_error"


class ProviderTimeoutError(ProviderError):
    """Request exceeded the configured timeout."""

    code = "provider_timeout_error"

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, retryable=True, **kwargs)


class ProviderRateLimitError(ProviderError):
    """Provider rejected the request due to rate limiting."""

    code = "provider_rate_limit_error"

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, retryable=True, **kwargs)


class ProviderUnavailableError(ProviderError):
    """Provider is temporarily unavailable."""

    code = "provider_unavailable_error"

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, retryable=True, **kwargs)
