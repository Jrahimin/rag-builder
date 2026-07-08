"""Startup validation for authentication configuration."""

from __future__ import annotations

from app.core.config import Settings

_MIN_SECRET_BYTES = 32


class AuthConfigurationError(RuntimeError):
    """Raised when auth-related settings are missing or insecure."""


def _secret_byte_length(value: str) -> int:
    return len(value.encode("utf-8"))


def validate_auth_config(settings: Settings) -> None:
    """Fail fast on insecure or incomplete auth configuration.

  When auth is disabled, production deployments are rejected so the API cannot
  start in an accidentally open state.
    """
    auth = settings.auth

    if not auth.enabled:
        if settings.app.is_production:
            msg = "APE_AUTH__ENABLED must be true in production."
            raise AuthConfigurationError(msg)
        return

    if not auth.admin_api_key:
        msg = "APE_AUTH__ADMIN_API_KEY is required when APE_AUTH__ENABLED=true."
        raise AuthConfigurationError(msg)

    if _secret_byte_length(auth.admin_api_key) < _MIN_SECRET_BYTES:
        msg = "APE_AUTH__ADMIN_API_KEY must be at least 32 bytes."
        raise AuthConfigurationError(msg)

    if not auth.key_pepper:
        msg = "APE_AUTH__KEY_PEPPER is required when APE_AUTH__ENABLED=true."
        raise AuthConfigurationError(msg)

    if _secret_byte_length(auth.key_pepper) < _MIN_SECRET_BYTES:
        msg = "APE_AUTH__KEY_PEPPER must be at least 32 bytes."
        raise AuthConfigurationError(msg)
