"""Unit tests for conversation HTTP helpers."""

from __future__ import annotations

import pytest

from app.api.v1.routes.conversations_router import _sse_error_message
from app.core.exceptions import BadRequestError, ServiceUnavailableError
from app.platform.providers.errors import ProviderError

pytestmark = pytest.mark.unit


def test_sse_error_message_maps_ape_error() -> None:
    message = _sse_error_message(
        BadRequestError(message="Unknown system prompt version: v9", code="unknown_prompt_version")
    )
    assert message == "Unknown system prompt version: v9"


def test_sse_error_message_sanitizes_provider_error() -> None:
    message = _sse_error_message(ProviderError("internal detail", provider_name="openai"))
    assert message == "The language model provider is temporarily unavailable."
    assert "internal detail" not in message


def test_sse_error_message_sanitizes_unexpected_error() -> None:
    message = _sse_error_message(RuntimeError("db password leaked"))
    assert message == "An unexpected error occurred."
    assert "password" not in message


def test_sse_error_message_maps_service_unavailable() -> None:
    message = _sse_error_message(
        ServiceUnavailableError(
            message="The language model provider is temporarily unavailable.",
            code="llm_provider_unavailable",
        )
    )
    assert "temporarily unavailable" in message
