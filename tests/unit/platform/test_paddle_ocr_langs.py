"""Unit tests for PaddleOCR language validation."""

from __future__ import annotations

import importlib.util

import pytest

from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.paddle_ocr_langs import (
    ensure_paddle_ocr_lang_supported,
    paddle_ocr_lang_is_supported,
)

pytestmark = pytest.mark.unit

paddleocr_installed = importlib.util.find_spec("paddleocr") is not None


@pytest.mark.skipif(not paddleocr_installed, reason="paddleocr optional extra not installed")
def test_bn_is_not_supported_by_stock_paddleocr() -> None:
    assert paddle_ocr_lang_is_supported("en") is True
    assert paddle_ocr_lang_is_supported("bn") is False


@pytest.mark.skipif(not paddleocr_installed, reason="paddleocr optional extra not installed")
def test_ensure_paddle_ocr_lang_supported_raises_for_bn() -> None:
    with pytest.raises(ProviderError, match="Bengali"):
        ensure_paddle_ocr_lang_supported("bn")
