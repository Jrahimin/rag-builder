"""Shared content hashing for reproducible artifact fingerprints."""

from __future__ import annotations

import hashlib


def content_hash(text: str) -> str:
    """Return a stable SHA-256 hex digest for text content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
