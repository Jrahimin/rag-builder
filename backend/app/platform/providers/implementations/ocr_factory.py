"""OCR provider factory and language-keyed provider pool."""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock

from app.core.config import OcrBackend, Settings, get_settings
from app.platform.domain.ocr_language import resolve_ocr_lang
from app.platform.providers.contracts.ocr import OcrImageInput, OcrPageResult, OCRProvider
from app.platform.providers.errors import ProviderError

_POOL_MAX_SIZE = 4
_NOOP_PROVIDER: OCRProvider | None = None


class NoopOCRProvider(OCRProvider):
    """Placeholder OCR provider when OCR is disabled."""

    @property
    def provider_name(self) -> str:
        return "noop"

    def recognize(self, image: OcrImageInput) -> OcrPageResult:
        del image
        msg = "OCR is disabled. Set APE_OCR__ENABLED=true and install requirements/ocr.txt."
        raise ProviderError(msg, provider_name=self.provider_name)


def create_ocr_provider(settings: Settings, *, lang: str | None = None) -> OCRProvider:
    """Build the configured OCR provider for a resolved language."""
    cfg = settings.ocr
    if not cfg.enabled or cfg.backend is OcrBackend.NOOP:
        return _get_noop_provider()
    resolved_lang = resolve_ocr_lang(lang, cfg.lang)
    if cfg.backend is OcrBackend.PADDLE:
        from app.platform.providers.implementations.paddle_ocr_provider import PaddleOCRProvider

        return PaddleOCRProvider(
            lang=resolved_lang,
            use_gpu=cfg.use_gpu,
        )
    msg = f"Unsupported OCR backend: {cfg.backend!r}"
    raise ProviderError(msg, provider_name="ocr_factory")


class _OcrProviderPool:
    """Small process-scoped pool of OCR providers keyed by backend, language, and GPU flag."""

    def __init__(self, max_size: int = _POOL_MAX_SIZE) -> None:
        self._max_size = max_size
        self._providers: OrderedDict[tuple[str, str, bool], OCRProvider] = OrderedDict()
        self._lock = Lock()

    def get(self, settings: Settings, lang: str) -> OCRProvider:
        key = (settings.ocr.backend.value, lang, settings.ocr.use_gpu)
        with self._lock:
            if key in self._providers:
                self._providers.move_to_end(key)
                return self._providers[key]

            provider = create_ocr_provider(settings, lang=lang)
            self._providers[key] = provider
            while len(self._providers) > self._max_size:
                self._providers.popitem(last=False)
            return provider

    def clear(self) -> None:
        with self._lock:
            self._providers.clear()


_pool = _OcrProviderPool()


def _get_noop_provider() -> OCRProvider:
    global _NOOP_PROVIDER
    if _NOOP_PROVIDER is None:
        _NOOP_PROVIDER = NoopOCRProvider()
    return _NOOP_PROVIDER


def get_ocr_provider(*, lang: str | None = None, settings: Settings | None = None) -> OCRProvider:
    """Return a cached OCR provider for the resolved language."""
    settings = settings or get_settings()
    if not settings.ocr.enabled or settings.ocr.backend is OcrBackend.NOOP:
        return _get_noop_provider()
    resolved = resolve_ocr_lang(lang, settings.ocr.lang)
    return _pool.get(settings, resolved)


def clear_ocr_provider_cache() -> None:
    """Clear the OCR provider pool (for tests and hot reload)."""
    _pool.clear()
    global _NOOP_PROVIDER
    _NOOP_PROVIDER = None
