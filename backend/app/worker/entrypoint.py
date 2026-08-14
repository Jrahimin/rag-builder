"""Taskiq worker entrypoint with all durable handlers registered in code."""

from app.worker.broker import broker
from app.worker.handlers import (  # noqa: F401
    corpus,
    document,
    document_lifecycle,
    embedding,
    evaluation,
    indexing,
    storage_reconciliation,
)

__all__ = ["broker"]
