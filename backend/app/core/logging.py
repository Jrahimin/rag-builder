"""Centralized, production-ready structured logging.

Built on `structlog` integrated with the standard library ``logging`` module
so that application logs *and* third-party logs (uvicorn, sqlalchemy, ...)
share one consistent pipeline.

Key features:
    * JSON output for production / log aggregation (``render_json=True``).
    * Colorized, human-friendly console output for local development.
    * Per-request context (``request_id``, ``trace_id``) automatically merged
      into every log line via context variables.

Usage::

    from app.core.logging import get_logger
    log = get_logger(__name__)
    log.info("document_indexed", document_id=doc_id, chunks=42)
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.core.config import Settings

_REQUEST_ID_KEY = "request_id"
_TRACE_ID_KEY = "trace_id"


def configure_logging(settings: Settings) -> None:
    """Configure structlog + stdlib logging for the whole process.

    Idempotent: safe to call multiple times (e.g. app startup and tests).
    """
    level = settings.logging.level.value
    render_json = settings.logging.render_json

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    # Processors shared by both structlog-native and stdlib ("foreign") records.
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if render_json
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # Route uvicorn logs through our pipeline instead of its own formatters.
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    # SQLAlchemy engine echo is controlled via config; keep its level sane here.
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.database.echo else logging.WARNING
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger, optionally namespaced."""
    return structlog.stdlib.get_logger(name)


def bind_request_context(*, request_id: str, trace_id: str) -> None:
    """Bind per-request identifiers to the logging context."""
    structlog.contextvars.bind_contextvars(**{_REQUEST_ID_KEY: request_id, _TRACE_ID_KEY: trace_id})


def clear_request_context() -> None:
    """Clear all context variables bound for the current request."""
    structlog.contextvars.clear_contextvars()
