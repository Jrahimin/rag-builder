"""API key generation and HMAC-SHA256 hashing with timing-safe verification."""

from __future__ import annotations

import hashlib
import hmac
import secrets

KEY_PREFIX = "ape_live_"


def generate_key() -> str:
    """Return a new raw API key (``ape_live_`` + url-safe random)."""
    return f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"


def key_display_prefix(raw_key: str, *, length: int = 16) -> str:
    """Return a short prefix for display and lookup hints."""
    return raw_key[:length]


def hash_key(raw_key: str, pepper: str) -> str:
    """Hash a raw key with HMAC-SHA256 using the deployment pepper."""
    return hmac.new(
        pepper.encode("utf-8"),
        raw_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_key(raw_key: str, pepper: str, stored_hash: str) -> bool:
    """Recompute the hash and compare with constant-time ``compare_digest``."""
    computed = hash_key(raw_key, pepper)
    return hmac.compare_digest(computed, stored_hash)
