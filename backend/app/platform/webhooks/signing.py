"""Canonical webhook payload serialization and HMAC-SHA256 signing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from typing import Any


def derive_endpoint_secret(master_key: str, endpoint_id: uuid.UUID) -> str:
    """Derive a stable endpoint secret without persisting customer secrets."""
    digest = hmac.new(
        master_key.encode("utf-8"),
        f"ape-webhook-endpoint:{endpoint_id}".encode(),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def canonical_webhook_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_webhook_payload(*, secret: str, timestamp: str, event_id: uuid.UUID, body: bytes) -> str:
    signed = timestamp.encode() + b"." + str(event_id).encode() + b"." + body
    return hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def verify_webhook_signature(
    *, secret: str, timestamp: str, event_id: uuid.UUID, body: bytes, signature: str
) -> bool:
    supplied = signature.removeprefix("v1=")
    expected = sign_webhook_payload(
        secret=secret,
        timestamp=timestamp,
        event_id=event_id,
        body=body,
    )
    return hmac.compare_digest(supplied, expected)
