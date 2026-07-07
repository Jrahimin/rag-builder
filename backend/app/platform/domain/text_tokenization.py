"""Shared Unicode-property text tokenization utilities."""

from __future__ import annotations

import regex

from app.platform.domain.text_normalizer import normalize_for_indexing, normalize_for_query

_TOKEN_PATTERN = regex.compile(r"[\p{Letter}\p{Number}\p{Mark}]+", regex.UNICODE)
_LATIN_PATTERN = regex.compile(r"\p{Latin}", regex.UNICODE)

TOKEN_COUNT_METHOD = "unicode_property_v1"


def _lowercase_latin_tokens(tokens: list[str]) -> list[str]:
    return [token.lower() if _LATIN_PATTERN.search(token) else token for token in tokens]


def normalize_text(text: str) -> str:
    """Normalize text for consistent indexing."""
    return normalize_for_indexing(text)


def tokenize(text: str, *, for_query: bool = False) -> list[str]:
    """Extract Unicode-property tokens from text."""
    normalized = normalize_for_query(text) if for_query else normalize_for_indexing(text)
    if not normalized:
        return []
    raw_tokens = _TOKEN_PATTERN.findall(normalized)
    return _lowercase_latin_tokens(raw_tokens)


def term_frequencies(tokens: list[str]) -> dict[str, int]:
    """Count term occurrences in a token list."""
    frequencies: dict[str, int] = {}
    for token in tokens:
        frequencies[token] = frequencies.get(token, 0) + 1
    return frequencies
