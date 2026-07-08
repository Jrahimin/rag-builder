"""Unit tests for API key crypto helpers."""

from __future__ import annotations

import pytest

from app.platform.domain.api_key_crypto import (
    KEY_PREFIX,
    generate_key,
    hash_key,
    key_display_prefix,
    verify_key,
)

pytestmark = pytest.mark.unit

PEPPER = "test-pepper-at-least-32-characters-long"


def test_generate_key_has_expected_prefix() -> None:
    key = generate_key()
    assert key.startswith(KEY_PREFIX)
    assert len(key) > len(KEY_PREFIX)


def test_hash_key_is_deterministic() -> None:
    raw = generate_key()
    assert hash_key(raw, PEPPER) == hash_key(raw, PEPPER)


def test_verify_key_accepts_valid_key() -> None:
    raw = generate_key()
    stored = hash_key(raw, PEPPER)
    assert verify_key(raw, PEPPER, stored)


def test_verify_key_rejects_wrong_key() -> None:
    stored = hash_key(generate_key(), PEPPER)
    assert not verify_key(generate_key(), PEPPER, stored)


def test_key_display_prefix() -> None:
    raw = "ape_live_abcdefghijklmnop"
    assert key_display_prefix(raw, length=12) == raw[:12]
