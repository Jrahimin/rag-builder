"""Heuristic language detection from script block ratios."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import regex

_BENGALI = regex.compile(r"\p{Bengali}", regex.UNICODE)
_LATIN = regex.compile(r"\p{Latin}", regex.UNICODE)
_ARABIC = regex.compile(r"\p{Arabic}", regex.UNICODE)
_DEVANAGARI = regex.compile(r"\p{Devanagari}", regex.UNICODE)
_HAN = regex.compile(r"\p{Han}", regex.UNICODE)
_LETTER = regex.compile(r"\p{Letter}", regex.UNICODE)

_SCRIPT_DETECTORS: tuple[tuple[str, regex.Pattern[str]], ...] = (
    ("bn", _BENGALI),
    ("en", _LATIN),
    ("ar", _ARABIC),
    ("hi", _DEVANAGARI),
    ("ja", _HAN),
)

_MIXED_THRESHOLD = 0.25

LANGUAGE_METADATA_SCHEMA_VERSION = "2026-08-18.v1"
ROUTING_LANGUAGE_MIXED = "mixed"
ROUTING_LANGUAGE_UNKNOWN = "unknown"
QUERY_PROFILE_LATIN_AMBIGUOUS = "latin_ambiguous"
DEFAULT_SUPPORTED_TARGET_LANGUAGES: tuple[str, ...] = ("bn", "en")
SYSTEM_LANGUAGE_METADATA_KEYS: tuple[str, ...] = (
    "document_language",
    "chunk_language",
    "chunk_language_confidence",
)

_QUOTED = regex.compile(r"[\"'“”‘’«»]([^\"'“”‘’«»]+)[\"'“”‘’«»]")  # noqa: RUF001
_PERCENT = regex.compile(r"[\d\p{Number}]+(?:[.,][\d\p{Number}]+)?\s*%")
_DATE = regex.compile(r"\b[\d\p{Number}]{1,4}[-/.][\d\p{Number}]{1,2}[-/.][\d\p{Number}]{1,4}\b")
_AMOUNT = regex.compile(
    r"(?:[$€£¥৳]|Tk\.?|BDT|USD|EUR)\s*[\d\p{Number}]+(?:[.,][\d\p{Number}]+)*|"
    r"[\d\p{Number}]+(?:[.,][\d\p{Number}]+)*\s*(?:[$€£¥৳]|Tk\.?|BDT|USD|EUR)",
    regex.IGNORECASE,
)
_ABBREVIATION = regex.compile(r"\b[A-Z]{2,}\b")
_NUMBER = regex.compile(r"[\d\p{Number}]+(?:[.,][\d\p{Number}]+)?")
_LATIN_WORD = regex.compile(r"\p{Latin}+(?:['\u2019-]\p{Latin}+)*", regex.UNICODE)
_ROMANIZED_BANGLA_PARTICLES = frozenset(
    {
        "ami",
        "apni",
        "ache",
        "ebong",
        "er",
        "hobe",
        "jonno",
        "ki",
        "kora",
        "kore",
        "koto",
        "nai",
        "niyom",
        "niyome",
        "te",
        "theke",
    }
)


@dataclass(frozen=True, slots=True)
class LanguageDetectionResult:
    """Detected language profile for a document or text sample."""

    primary_language: str | None
    confidence: float
    languages: dict[str, float]
    is_mixed: bool


@dataclass(frozen=True, slots=True)
class QueryLanguageProfile:
    """Conservative query-language signals used only for retrieval routing."""

    profile: str
    exact_primary: str | None
    confidence: float
    languages: dict[str, float]
    is_mixed: bool
    is_latin_ambiguous: bool
    is_romanized_or_codeswitched: bool = False


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


def routing_language_from_detection(result: LanguageDetectionResult) -> str:
    """Map a detection result onto a retrieval routing code."""
    if result.primary_language is None:
        return ROUTING_LANGUAGE_UNKNOWN
    if result.is_mixed or result.primary_language == ROUTING_LANGUAGE_MIXED:
        return ROUTING_LANGUAGE_MIXED
    return result.primary_language


def normalize_routing_language(value: object) -> str:
    """Normalize stored language metadata to a routing code."""
    if value is None:
        return ROUTING_LANGUAGE_UNKNOWN
    text = str(value).strip().lower()
    if not text or text in {"none", "null", ROUTING_LANGUAGE_UNKNOWN}:
        return ROUTING_LANGUAGE_UNKNOWN
    if text == ROUTING_LANGUAGE_MIXED:
        return ROUTING_LANGUAGE_MIXED
    return text


def detect_query_language_profile(text: str) -> QueryLanguageProfile:
    """Resolve a query profile without treating Latin-only text as English."""
    detected = detect_language(text)
    if detected.primary_language is None:
        return QueryLanguageProfile(
            profile=ROUTING_LANGUAGE_UNKNOWN,
            exact_primary=None,
            confidence=detected.confidence,
            languages=dict(detected.languages),
            is_mixed=False,
            is_latin_ambiguous=False,
        )
    if detected.is_mixed:
        return QueryLanguageProfile(
            profile=ROUTING_LANGUAGE_MIXED,
            exact_primary=None,
            confidence=detected.confidence,
            languages=dict(detected.languages),
            is_mixed=True,
            is_latin_ambiguous=False,
        )
    if detected.primary_language == "en":
        words = {word.casefold() for word in _LATIN_WORD.findall(text)}
        romanized = len(words & _ROMANIZED_BANGLA_PARTICLES) >= 2
        return QueryLanguageProfile(
            profile=QUERY_PROFILE_LATIN_AMBIGUOUS,
            exact_primary=None,
            confidence=detected.confidence,
            languages=dict(detected.languages),
            is_mixed=False,
            is_latin_ambiguous=True,
            is_romanized_or_codeswitched=romanized,
        )
    return QueryLanguageProfile(
        profile=detected.primary_language,
        exact_primary=detected.primary_language,
        confidence=detected.confidence,
        languages=dict(detected.languages),
        is_mixed=False,
        is_latin_ambiguous=False,
    )


def select_translation_target(
    profile: QueryLanguageProfile,
    inventory_counts: Mapping[str, int],
    supported_targets: Sequence[str] = DEFAULT_SUPPORTED_TARGET_LANGUAGES,
) -> str | None:
    """Pick at most one exact corpus language to translate into.

    Translation is used only when it can materially improve retrieval:
    Bangla → English, or a bounded Banglish/code-switched rewrite to English.
    Ordinary Latin-script queries, mixed-script queries, and same-language
    inventories skip the rewrite. Original dense + lexical always remain.
    """
    exact_inventory = {
        language: count
        for language, count in inventory_counts.items()
        if language not in {ROUTING_LANGUAGE_MIXED, ROUTING_LANGUAGE_UNKNOWN} and count > 0
    }
    candidates = [language for language in supported_targets if language in exact_inventory]
    if profile.exact_primary is not None:
        candidates = [language for language in candidates if language != profile.exact_primary]
    elif profile.is_mixed:
        return None
    elif profile.is_romanized_or_codeswitched:
        candidates = [language for language in candidates if language == "en"]
    else:
        return None
    if not candidates:
        return None
    return sorted(candidates, key=lambda language: (-exact_inventory[language], language))[0]


def extract_protected_literals(text: str) -> tuple[str, ...]:
    """Literals that a retrieval translation must preserve exactly."""
    found: list[str] = []
    for pattern in (_QUOTED, _PERCENT, _DATE, _AMOUNT, _ABBREVIATION, _NUMBER):
        found.extend(match.group(0) for match in pattern.finditer(text))
    unique: list[str] = []
    seen: set[str] = set()
    for literal in found:
        stripped = literal.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        unique.append(stripped)
    return tuple(unique)


def missing_protected_literals(original: str, translated: str) -> tuple[str, ...]:
    """Return protected literals from the original query that the translation dropped."""
    translated_text = translated
    normalized_translated = _normalize_unicode_digits(translated_text)
    exact_literals = {
        match.group(0).strip()
        for pattern in (_QUOTED, _ABBREVIATION)
        for match in pattern.finditer(original)
    }
    missing: list[str] = []
    for literal in extract_protected_literals(original):
        if literal in translated_text:
            continue
        # Human-readable numbers, dates, percentages, and amounts may validly
        # change numeral scripts during translation.  Codes and abbreviations
        # are still emitted as separate protected literals and remain exact.
        if _normalize_unicode_digits(literal) in normalized_translated:
            continue
        if literal not in exact_literals and _numeric_literal_equivalent(
            literal, normalized_translated
        ):
            continue
        if literal not in translated_text:
            missing.append(literal)
    return tuple(missing)


def _normalize_unicode_digits(text: str) -> str:
    output: list[str] = []
    for char in text:
        try:
            output.append(str(unicodedata.digit(char)))
        except (TypeError, ValueError):
            output.append(char)
    return "".join(output)


def _numeric_literal_equivalent(literal: str, normalized_translated: str) -> bool:
    normalized_literal = _normalize_unicode_digits(literal)
    numbers = [_canonical_number(item) for item in _NUMBER.findall(normalized_literal)]
    if not numbers:
        return False
    translated_numbers = {
        _canonical_number(item) for item in _NUMBER.findall(normalized_translated)
    }
    if not set(numbers).issubset(translated_numbers):
        return False
    if "%" in normalized_literal and "%" not in normalized_translated:
        return False
    currency_symbols = set(normalized_literal) & set("$€£¥৳")
    return not currency_symbols or currency_symbols.issubset(set(normalized_translated))


def _canonical_number(value: str) -> str:
    compact = value.replace(",", "")
    if "." in compact:
        whole, fraction = compact.split(".", maxsplit=1)
        return f"{int(whole or '0')}.{fraction.rstrip('0') or '0'}"
    return str(int(compact or "0"))


def build_index_language_snapshot(
    *,
    content: str,
    chunk_metadata: Mapping[str, object],
    document_language: str | None,
) -> dict[str, str]:
    """System language fields copied into keyword-index snapshots during a build."""
    document_lang = normalize_routing_language(
        chunk_metadata.get("document_language") or document_language
    )
    stored_chunk_language = chunk_metadata.get("chunk_language")
    if stored_chunk_language is not None:
        chunk_language = normalize_routing_language(stored_chunk_language)
        confidence = chunk_metadata.get("chunk_language_confidence", 0.0)
    else:
        detected = detect_language(content)
        chunk_language = routing_language_from_detection(detected)
        confidence = detected.confidence
    return {
        "document_language": document_lang,
        "chunk_language": chunk_language,
        "chunk_language_confidence": str(confidence),
    }
