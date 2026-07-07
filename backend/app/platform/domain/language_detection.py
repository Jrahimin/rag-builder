"""Heuristic language detection from script block ratios."""

from __future__ import annotations

from dataclasses import dataclass

import regex

_BENGALI = regex.compile(r"\p{Bengali}", regex.UNICODE)
_LATIN = regex.compile(r"\p{Latin}", regex.UNICODE)
_ARABIC = regex.compile(r"\p{Arabic}", regex.UNICODE)
_DEVANAGARI = regex.compile(r"\p{Devanagari}", regex.UNICODE)
_HAN = regex.compile(r"\p{Han}", regex.UNICODE)
_LETTER = regex.compile(r"\p{Letter}", regex.UNICODE)

_SCRIPT_DETECTORS: tuple[tuple[str, regex.Pattern], ...] = (
    ("bn", _BENGALI),
    ("en", _LATIN),
    ("ar", _ARABIC),
    ("hi", _DEVANAGARI),
    ("ja", _HAN),
)

_MIXED_THRESHOLD = 0.25


@dataclass(frozen=True, slots=True)
class LanguageDetectionResult:
    """Detected language profile for a document or text sample."""

    primary_language: str | None
    confidence: float
    languages: dict[str, float]
    is_mixed: bool


def detect_language(text: str) -> LanguageDetectionResult:
    """Detect primary language using Unicode script ratios."""
    letters = _LETTER.findall(text)
    if not letters:
        return LanguageDetectionResult(
            primary_language=None,
            confidence=0.0,
            languages={},
            is_mixed=False,
        )

    total = len(letters)
    ratios: dict[str, float] = {}
    for code, pattern in _SCRIPT_DETECTORS:
        count = len(pattern.findall(text))
        if count:
            ratios[code] = round(count / total, 4)

    if not ratios:
        return LanguageDetectionResult(
            primary_language=None,
            confidence=0.0,
            languages={},
            is_mixed=False,
        )

    primary = max(ratios, key=ratios.get)  # type: ignore[arg-type]
    primary_ratio = ratios[primary]
    secondary_ratios = [value for key, value in ratios.items() if key != primary]
    is_mixed = bool(secondary_ratios) and max(secondary_ratios) >= _MIXED_THRESHOLD
    confidence = primary_ratio if not is_mixed else primary_ratio * 0.85

    return LanguageDetectionResult(
        primary_language="mixed" if is_mixed else primary,
        confidence=round(min(confidence, 1.0), 4),
        languages=ratios,
        is_mixed=is_mixed,
    )
