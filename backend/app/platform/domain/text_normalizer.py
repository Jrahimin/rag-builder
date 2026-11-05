"""Shared text normalization for ingestion, indexing, and query paths."""

from __future__ import annotations

import re
import unicodedata

import regex

# Quote and dash variants normalized to ASCII forms for consistent matching.
_QUOTE_MAP = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u00ab": '"',
        "\u00bb": '"',
    }
)
_DASH_PATTERN = re.compile(r"[\u2010-\u2015\u2212]")
_ELLIPSIS_PATTERN = re.compile(r"\.{3,}")
_HYPHENATED_LINE_BREAK = regex.compile(r"(\p{Letter})-\s*\n\s*(\p{Letter})", regex.UNICODE)
_SOFT_LINE_BREAK = regex.compile(r"(?<=\p{Letter})\n(?=\p{Letter})", regex.UNICODE)
_MIXED_SCRIPT_BOUNDARY = regex.compile(
    r"([\p{Latin}])([\p{Bengali}\p{Devanagari}\p{Arabic}\p{Han}])|"
    r"([\p{Bengali}\p{Devanagari}\p{Arabic}\p{Han}])([\p{Latin}])",
    regex.UNICODE,
)
_WHITESPACE_PATTERN = re.compile(r"\s+")


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _normalize_punctuation(text: str) -> str:
    text = text.translate(_QUOTE_MAP)
    text = _DASH_PATTERN.sub("-", text)
    text = _ELLIPSIS_PATTERN.sub("\u2026", text)
    return text


def _ocr_cleanup(text: str) -> str:
    text = _HYPHENATED_LINE_BREAK.sub(r"\1\2", text)
    text = _SOFT_LINE_BREAK.sub(" ", text)
    return text


def _fix_mixed_script_spacing(text: str) -> str:
    def _insert_space(match: regex.Match[str]) -> str:
        groups = match.groups()
        if groups[0] and groups[1]:
            return f"{groups[0]} {groups[1]}"
        return f"{groups[2]} {groups[3]}"

    return _MIXED_SCRIPT_BOUNDARY.sub(_insert_space, text)


def _collapse_whitespace(text: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", text).strip()


def normalize_for_storage(text: str) -> str:
    """Parser output cleanup — NFC, OCR fixes, punctuation, whitespace."""
    if not text:
        return ""
    normalized = _nfc(text)
    normalized = _ocr_cleanup(normalized)
    normalized = _normalize_punctuation(normalized)
    normalized = _fix_mixed_script_spacing(normalized)
    return _collapse_whitespace(normalized)


LEXICAL_NORMALIZATION_VERSION = "unicode_numeral_alias_v2"


def normalize_for_indexing(text: str) -> str:
    """Separate lexical representation retains original numerals plus ASCII aliases."""
    original = normalize_for_storage(text)
    aliases = []
    for token in regex.findall(r"[\p{Letter}\p{Number}\p{Mark}]+", original):
        alias = "".join(
            str(unicodedata.decimal(char)) if char.isdecimal() else char for char in token
        )
        if alias != token:
            aliases.append(alias)
    return " ".join([original, *aliases]).strip()


def normalize_for_query(text: str) -> str:
    """Search query path uses the same versioned numeral aliases as indexing."""
    return normalize_for_indexing(text)


def normalize_text(text: str) -> str:
    """Backward-compatible alias for indexing normalization."""
    return normalize_for_indexing(text)
