"""PaddleOCR language support helpers."""

from __future__ import annotations

from app.platform.providers.errors import ProviderError

_PROVIDER_NAME = "paddle"


def paddle_ocr_lang_is_supported(lang: str) -> bool:
    """Return whether the installed PaddleOCR build has stock models for ``lang``."""
    try:
        from paddleocr._pipelines.ocr import PaddleOCR
    except ImportError:
        return False

    engine = PaddleOCR.__new__(PaddleOCR)
    det_model, rec_model = engine._get_ocr_model_names(lang, None)
    return det_model is not None and rec_model is not None


def ensure_paddle_ocr_lang_supported(lang: str) -> None:
    """Raise :class:`ProviderError` when ``lang`` has no stock PaddleOCR models."""
    if paddle_ocr_lang_is_supported(lang):
        return

    if lang == "bn":
        msg = (
            "PaddleOCR 3.7 does not ship stock Bengali (bn) models. "
            "Set APE_OCR__LANG=en (or another supported code such as hi, ta, en) "
            "or train/install a custom Bengali recognition model."
        )
        raise ProviderError(msg, provider_name=_PROVIDER_NAME)

    msg = (
        f"PaddleOCR has no stock models for lang={lang!r}. "
        "See PaddleOCR multilingual docs for supported language codes."
    )
    raise ProviderError(msg, provider_name=_PROVIDER_NAME)
