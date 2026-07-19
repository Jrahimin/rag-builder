"""Webhook canonicalization and signature contract tests."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.modules.webhooks.schemas.webhook import WebhookEndpointCreate
from app.platform.webhooks.contracts import WebhookEventType
from app.platform.webhooks.signing import (
    canonical_webhook_body,
    derive_endpoint_secret,
    sign_webhook_payload,
    verify_webhook_signature,
)

pytestmark = pytest.mark.unit


def test_signature_is_canonical_and_detects_tampering() -> None:
    endpoint_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    event_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    secret = derive_endpoint_secret("a" * 32, endpoint_id)
    body = canonical_webhook_body({"z": "বাংলা", "a": {"value": 1}})
    assert body == '{"a":{"value":1},"z":"বাংলা"}'.encode()
    signature = sign_webhook_payload(
        secret=secret,
        timestamp="1720000000",
        event_id=event_id,
        body=body,
    )
    assert verify_webhook_signature(
        secret=secret,
        timestamp="1720000000",
        event_id=event_id,
        body=body,
        signature=f"v1={signature}",
    )
    assert not verify_webhook_signature(
        secret=secret,
        timestamp="1720000000",
        event_id=event_id,
        body=body + b" ",
        signature=f"v1={signature}",
    )


def test_endpoint_secrets_are_stable_and_isolated() -> None:
    first = uuid.uuid4()
    second = uuid.uuid4()
    assert derive_endpoint_secret("m" * 32, first) == derive_endpoint_secret("m" * 32, first)
    assert derive_endpoint_secret("m" * 32, first) != derive_endpoint_secret("m" * 32, second)


@pytest.mark.parametrize(
    "url",
    ["https://user:password@example.test/ape", "https://example.test/ape#secret"],
)
def test_endpoint_url_rejects_embedded_secrets_and_fragments(url: str) -> None:
    with pytest.raises(ValidationError):
        WebhookEndpointCreate(
            url=url,
            event_types=[WebhookEventType.DOCUMENT_PROCESSING_SUCCEEDED_V1],
        )
