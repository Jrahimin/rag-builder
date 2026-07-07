"""Integration-style tests for multilingual fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.platform.domain.text_tokenization import tokenize

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "multilingual"


@pytest.mark.parametrize(
    ("filename", "expected_token"),
    [
        ("bangla_refund.txt", "রিফান্ড"),
        ("english_refund.txt", "refund"),
        ("mixed_refund.txt", "রিফান্ড"),
    ],
)
def test_fixture_tokenization(filename: str, expected_token: str) -> None:
    text = (FIXTURES / filename).read_text(encoding="utf-8")
    tokens = tokenize(text)
    assert tokens
    assert expected_token in tokens
