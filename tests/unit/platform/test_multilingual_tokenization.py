"""Unit tests for Unicode-property tokenization and normalization."""

from __future__ import annotations

import pytest

from app.platform.domain.language_detection import detect_language
from app.platform.domain.text_normalizer import normalize_for_indexing, normalize_for_query
from app.platform.domain.text_tokenization import tokenize

pytestmark = pytest.mark.unit

BANGLA_SAMPLE = "রিফান্ড নীতি ৩০ দিনের মধ্যে প্রযোজ্য।"
ENGLISH_SAMPLE = "Refund Policy v2.0 — 30-day window!"
ARABIC_SAMPLE = "سياسة الاسترداد خلال 30 يوماً."
MIXED_SAMPLE = "Refund policy রিফান্ড নীতি applies within 30 days।"
CODE_SWITCHING_SAMPLE = "আজকের stock price হতে পারে"


def test_bangla_tokenize_non_zero() -> None:
    tokens = tokenize(BANGLA_SAMPLE)
    assert tokens
    assert "রিফান্ড" in tokens


def test_english_tokenize_lowercases_latin() -> None:
    tokens = tokenize(ENGLISH_SAMPLE)
    assert "refund" in tokens
    assert "policy" in tokens


def test_arabic_tokenize_non_zero() -> None:
    tokens = tokenize(ARABIC_SAMPLE)
    assert tokens


def test_mixed_language_tokenize_both_scripts() -> None:
    tokens = tokenize(MIXED_SAMPLE)
    assert "refund" in tokens
    assert "রিফান্ড" in tokens


def test_query_and_index_normalization_match() -> None:
    raw = "  Refund   Policy!!!  "
    assert normalize_for_query(raw) == normalize_for_indexing(raw)
    assert tokenize(raw, for_query=True) == tokenize(raw)


def test_ellipsis_sentence_boundary_normalization() -> None:
    normalized = normalize_for_indexing("Wait... really")
    assert "…" in normalized


def test_detect_language_bangla() -> None:
    result = detect_language(BANGLA_SAMPLE)
    assert result.primary_language == "bn"
    assert result.confidence > 0.5


def test_detect_language_mixed() -> None:
    result = detect_language(MIXED_SAMPLE)
    assert result.is_mixed is True
    assert result.primary_language == "mixed"


def test_code_switching_bangla_english_inline() -> None:
    """Bangla + Latin in one sentence (e.g. আজকের stock price হতে পারে)."""
    tokens = tokenize(CODE_SWITCHING_SAMPLE)
    assert "stock" in tokens
    assert "price" in tokens
    assert any("\u0986" <= ch <= "\u09FF" for token in tokens for ch in token)
    result = detect_language(CODE_SWITCHING_SAMPLE)
    assert result.is_mixed is True
