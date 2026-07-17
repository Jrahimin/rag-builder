"""OCR language normalization shared by ingestion and provider resolution."""

from __future__ import annotations

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
