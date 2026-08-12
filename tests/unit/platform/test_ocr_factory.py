"""Unit tests for OCR provider factory and language pool."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.core.config import OcrBackend, OcrConfig, Settings
from app.platform.domain.ocr_language import (
    is_ocr_first_language,
    normalize_stored_ocr_lang,
    resolve_document_language,
    resolve_ocr_lang,
)
from app.platform.providers.contracts.ocr import OcrImageInput, OcrPageResult, OCRProvider
from app.platform.providers.errors import ProviderAuthenticationError
from app.platform.providers.implementations.ocr_factory import (
    clear_ocr_provider_cache,
    create_ocr_provider,
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
            bangla_backend=OcrBackend.GOOGLE_VISION,
            google_api_key="test-key",
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


@pytest.mark.parametrize(
    ("explicit", "sample", "default", "expected"),
    [
        ("english", "বাংলা ভাষার লেখা", "bn", ("en", "explicit")),
        (None, "বাংলা ভাষার লেখা with English", "en", ("bn", "detected")),
        (None, "An ordinary English document", "bn", ("en", "detected")),
        (None, "1234 --", "bn", ("bn", "default")),
    ],
)
def test_resolve_document_language_precedence(
    explicit: str | None,
    sample: str,
    default: str,
    expected: tuple[str, str],
) -> None:
    assert (
        resolve_document_language(
            explicit=explicit,
            sample_text=sample,
            default_lang=default,
            bangla_min_ratio=0.1,
        )
        == expected
    )
    assert is_ocr_first_language("bangla")


def test_resolve_document_language_routes_legacy_bangla_fonts_to_bangla_ocr() -> None:
    assert (
        resolve_document_language(
            explicit=None,
            sample_text="evsjv‡`k †M‡RU AwZwi³ msL¨v",
            default_lang="en",
            bangla_min_ratio=0.1,
            font_names=("SutonnyMJ", "NikoshBAN"),
        )
        == ("bn", "legacy_font")
    )


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


def test_bangla_only_google_backend_does_not_collapse_to_noop() -> None:
    clear_ocr_provider_cache()
    settings = Settings(
        ocr=OcrConfig(
            enabled=True,
            backend=OcrBackend.NOOP,
            bangla_backend=OcrBackend.GOOGLE_VISION,
            google_api_key="test-key",
        )
    )

    provider = get_ocr_provider(lang="bn", settings=settings)

    assert provider.provider_name == "google_vision"
    clear_ocr_provider_cache()


def test_google_backend_requires_api_key() -> None:
    settings = Settings(
        ocr=OcrConfig(
            enabled=True,
            bangla_backend=OcrBackend.GOOGLE_VISION,
        )
    )
    with pytest.raises(ProviderAuthenticationError):
        create_ocr_provider(settings, lang="bn")
