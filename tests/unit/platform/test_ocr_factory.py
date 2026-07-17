"""Unit tests for OCR provider factory and language pool."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.core.config import OcrBackend, OcrConfig, Settings
from app.platform.domain.ocr_language import normalize_stored_ocr_lang, resolve_ocr_lang
from app.platform.providers.contracts.ocr import OcrImageInput, OcrPageResult, OCRProvider
from app.platform.providers.implementations.ocr_factory import (
    clear_ocr_provider_cache,
    get_ocr_provider,
)

pytestmark = pytest.mark.unit


@dataclass
class _FakeOCRProvider(OCRProvider):
    lang: str

    @property
    def provider_name(self) -> str:
        return f"fake-{self.lang}"

    def recognize(self, image: OcrImageInput) -> OcrPageResult:
        del image
        return OcrPageResult(text="", confidence=0.0, provider_name=self.provider_name)


def _settings(*, default_lang: str = "en") -> Settings:
    return Settings(
        ocr=OcrConfig(
            enabled=True,
            backend=OcrBackend.PADDLE,
            lang=default_lang,
        ),
    )


def test_resolve_ocr_lang_uses_default_and_aliases() -> None:
    assert resolve_ocr_lang(None, "en") == "en"
    assert resolve_ocr_lang("eng", "en") == "en"
    assert resolve_ocr_lang("bn", "en") == "bn"
    assert resolve_ocr_lang("bangla", "en") == "bn"


def test_normalize_stored_ocr_lang_blank_to_none() -> None:
    assert normalize_stored_ocr_lang(None) is None
    assert normalize_stored_ocr_lang("  ") is None
    assert normalize_stored_ocr_lang("eng") == "en"


def test_get_ocr_provider_pools_by_language(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_ocr_provider_cache()
    created_langs: list[str] = []

    def fake_create(settings: Settings, *, lang: str | None = None) -> OCRProvider:
        resolved = resolve_ocr_lang(lang, settings.ocr.lang)
        created_langs.append(resolved)
        return _FakeOCRProvider(lang=resolved)

    monkeypatch.setattr(
        "app.platform.providers.implementations.ocr_factory.create_ocr_provider",
        fake_create,
    )

    settings = _settings(default_lang="en")
    first_en = get_ocr_provider(lang=None, settings=settings)
    second_en = get_ocr_provider(lang="eng", settings=settings)
    bn = get_ocr_provider(lang="bn", settings=settings)

    assert first_en is second_en
    assert bn is not first_en
    assert created_langs == ["en", "bn"]
    assert first_en.provider_name == "fake-en"
    assert bn.provider_name == "fake-bn"

    clear_ocr_provider_cache()
