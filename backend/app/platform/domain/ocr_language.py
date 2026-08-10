"""OCR language normalization shared by ingestion and provider resolution."""

from __future__ import annotations

from app.platform.domain.language_detection import detect_language

_OCR_LANG_ALIASES: dict[str, str] = {
    "eng": "en",
    "english": "en",
    "ben": "bn",
    "bengali": "bn",
    "bangla": "bn",
}


def resolve_ocr_lang(document_ocr_lang: str | None, default_lang: str) -> str:
    """Resolve a per-document OCR language with the deployment default fallback."""
    raw = (document_ocr_lang if document_ocr_lang is not None else default_lang).strip().lower()
    if not raw:
        raw = default_lang.strip().lower()
    return _OCR_LANG_ALIASES.get(raw, raw)


def normalize_stored_ocr_lang(value: str | None) -> str | None:
    """Normalize an optional upload/reprocess OCR language for persistence."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return resolve_ocr_lang(stripped, "en")


def resolve_document_language(
    *,
    explicit: str | None,
    sample_text: str,
    default_lang: str,
    bangla_min_ratio: float,
) -> tuple[str, str]:
    """Resolve document language and report whether it was explicit, detected, or default."""
    if explicit is not None and explicit.strip():
        return resolve_ocr_lang(explicit, default_lang), "explicit"

    detected = detect_language(sample_text)
    if detected.languages.get("bn", 0.0) >= bangla_min_ratio:
        return "bn", "detected"
    if detected.primary_language and detected.primary_language != "mixed":
        return resolve_ocr_lang(detected.primary_language, default_lang), "detected"
    if detected.languages:
        primary = max(detected.languages, key=detected.languages.get)  # type: ignore[arg-type]
        return resolve_ocr_lang(primary, default_lang), "detected"
    return resolve_ocr_lang(None, default_lang), "default"


def is_ocr_first_language(language: str) -> bool:
    """Return whether a language uses the document-wide OCR-first route."""
    return resolve_ocr_lang(language, "en") == "bn"
