"""PaddleOCR implementation of OCRProvider."""

from __future__ import annotations

from typing import Any

from app.platform.domain.text_normalizer import normalize_for_storage
from app.platform.providers.contracts.ocr import OcrImageInput, OcrPageResult, OCRProvider
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.paddle_ocr_langs import ensure_paddle_ocr_lang_supported

_PROVIDER_NAME = "paddle"


class PaddleOCRProvider(OCRProvider):
    """PaddleOCR 3.x adapter."""

    def __init__(self, *, lang: str = "en", use_gpu: bool = False) -> None:
        del use_gpu  # PaddleOCR 3.x selects device via Paddle runtime, not this kwarg.
        ensure_paddle_ocr_lang_supported(lang)

        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            msg = "PaddleOCR is not installed. Install backend/requirements/ocr.txt."
            raise ProviderError(msg, provider_name=_PROVIDER_NAME) from exc

        try:
            self._engine = PaddleOCR(
                lang=lang,
                use_textline_orientation=True,
            )
        except ValueError as exc:
            raise ProviderError(str(exc), provider_name=_PROVIDER_NAME) from exc

    @property
    def provider_name(self) -> str:
        return _PROVIDER_NAME

    def recognize(self, image: OcrImageInput) -> OcrPageResult:
        try:
            import numpy as np
            from PIL import Image
        except ImportError as exc:
            msg = "Pillow and numpy are required for PaddleOCR image decoding."
            raise ProviderError(msg, provider_name=_PROVIDER_NAME) from exc

        try:
            pil_image = Image.open(__import__("io").BytesIO(image.data))
            array = np.array(pil_image.convert("RGB"))
            results = self._engine.predict(array)
        except Exception as exc:
            msg = "PaddleOCR failed to process image."
            raise ProviderError(msg, provider_name=_PROVIDER_NAME) from exc

        lines, confidences = _extract_predict_lines(results)
        combined = normalize_for_storage("\n".join(lines))
        confidence = sum(confidences) / len(confidences) if confidences else 0.0
        return OcrPageResult(
            text=combined,
            confidence=round(confidence, 4),
            provider_name=self.provider_name,
            lines=tuple(lines),
            page_number=image.page_number,
        )


def _extract_predict_lines(results: Any) -> tuple[list[str], list[float]]:
    """Parse PaddleOCR 3.x ``predict`` output into text lines and scores."""
    lines: list[str] = []
    confidences: list[float] = []
    for page in results or []:
        if not isinstance(page, dict):
            continue
        rec_texts = page.get("rec_texts") or []
        rec_scores = page.get("rec_scores") or []
        for text, score in zip(rec_texts, rec_scores, strict=False):
            normalized = str(text).strip()
            if normalized:
                lines.append(normalized)
                confidences.append(float(score))
    return lines, confidences
