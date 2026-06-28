"""Unit tests for logging configuration."""

from __future__ import annotations

import logging

import pytest

from app.core.config import LogLevel, get_settings
from app.core.logging import configure_logging, get_logger

pytestmark = pytest.mark.unit


def test_configure_logging_sets_root_level() -> None:
    settings = get_settings()
    configure_logging(settings)
    expected = getattr(logging, settings.logging.level.value)
    assert logging.getLogger().level == expected


def test_configure_logging_json_renderer_is_idempotent() -> None:
    settings = get_settings()
    settings.logging.render_json = True
    configure_logging(settings)
    configure_logging(settings)  # second call must not raise
    assert logging.getLogger().handlers


def test_get_logger_emits_without_error(capsys: pytest.CaptureFixture[str]) -> None:
    settings = get_settings()
    settings.logging.level = LogLevel.INFO
    configure_logging(settings)
    log = get_logger("test.logger")
    log.info("hello_event", key="value")
    captured = capsys.readouterr()
    assert "hello_event" in captured.out
