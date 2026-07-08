"""Extract API keys from HTTP request headers."""

from __future__ import annotations

from starlette.requests import Request

from app.platform.domain.api_key_crypto import KEY_PREFIX

_BEARER_PREFIX = "Bearer "


def extract_api_key(request: Request) -> str | None:
    """Return the raw API key from ``Authorization: Bearer`` or ``X-API-Key``."""
    header = request.headers.get("Authorization")
    if header and header.startswith(_BEARER_PREFIX):
        token = header[len(_BEARER_PREFIX) :].strip()
        if token:
            return token

    api_key = request.headers.get("X-API-Key")
    if api_key and api_key.strip():
        return api_key.strip()

    return None


def is_api_key_format(value: str) -> bool:
    """Return whether ``value`` matches the expected API key prefix."""
    return value.startswith(KEY_PREFIX)
