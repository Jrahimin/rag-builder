"""Google Cloud Vision implementation of the OCR provider contract."""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.platform.domain.text_normalizer import normalize_for_storage
from app.platform.providers.contracts.ocr import OcrImageInput, OcrPageResult, OCRProvider
from app.platform.providers.errors import (
    ProviderAuthenticationError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

_PROVIDER_NAME = "google_vision"
_FEATURE_TYPE = "DOCUMENT_TEXT_DETECTION"


class GoogleVisionOCRProvider(OCRProvider):
    """Recognize document text through Google Cloud Vision's REST API."""

    def __init__(
        self,
        *,
        api_key: str | None,
        endpoint: str,
        timeout_seconds: float,
        max_attempts: int,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not (api_key or "").strip():
            raise ProviderAuthenticationError(
                "Google Vision OCR requires a non-empty API key.",
                provider_name=_PROVIDER_NAME,
            )
        self._api_key = (api_key or "").strip()
        self._endpoint = endpoint
        self._max_attempts = max_attempts
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._sleep = sleep

    @property
    def provider_name(self) -> str:
        return _PROVIDER_NAME

    def recognize(self, image: OcrImageInput) -> OcrPageResult:
        payload = {
            "requests": [
                {
                    "image": {"content": base64.b64encode(image.data).decode("ascii")},
                    "features": [{"type": _FEATURE_TYPE}],
                }
            ]
        }
        annotation: dict[str, Any] | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.post(
                    self._endpoint,
                    params={"key": self._api_key},
                    json=payload,
                )
                annotation = self._parse_response(response)
                break
            except ProviderError as exc:
                if not exc.retryable or attempt >= self._max_attempts:
                    raise
                self._sleep(_backoff_seconds(attempt))
            except httpx.TimeoutException as exc:
                timeout_error = ProviderTimeoutError(
                    "Google Vision OCR request timed out.",
                    provider_name=self.provider_name,
                )
                if attempt >= self._max_attempts:
                    raise timeout_error from exc
                self._sleep(_backoff_seconds(attempt))
            except httpx.RequestError as exc:
                connection_error = ProviderConnectionError(
                    "Google Vision OCR connection failed.",
                    provider_name=self.provider_name,
                )
                if attempt >= self._max_attempts:
                    raise connection_error from exc
                self._sleep(_backoff_seconds(attempt))

        if annotation is None:  # pragma: no cover - loop always returns or raises
            raise ProviderError(
                "Google Vision OCR returned no annotation.",
                provider_name=self.provider_name,
            )

        full_text = annotation.get("fullTextAnnotation")
        if full_text is None:
            raw_text = ""
            confidence = 0.0
        elif not isinstance(full_text, dict):
            raise ProviderError(
                "Google Vision OCR returned a malformed fullTextAnnotation.",
                provider_name=self.provider_name,
            )
        else:
            raw_value = full_text.get("text", "")
            if not isinstance(raw_value, str):
                raise ProviderError(
                    "Google Vision OCR returned malformed text.",
                    provider_name=self.provider_name,
                )
            raw_text = raw_value
            confidence = _mean_block_confidence(full_text)

        lines = tuple(
            normalized
            for line in raw_text.splitlines()
            if (normalized := normalize_for_storage(line))
        )
        return OcrPageResult(
            text=normalize_for_storage(raw_text),
            confidence=confidence,
            provider_name=self.provider_name,
            lines=lines,
            page_number=image.page_number,
        )

    def _parse_response(self, response: httpx.Response) -> dict[str, Any]:
        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError(
                "Google Vision OCR rejected the configured credentials.",
                provider_name=self.provider_name,
            )
        if response.status_code == 429:
            raise ProviderRateLimitError(
                "Google Vision OCR rate limit exceeded.",
                provider_name=self.provider_name,
            )
        if response.status_code >= 500:
            raise ProviderUnavailableError(
                "Google Vision OCR is temporarily unavailable.",
                provider_name=self.provider_name,
            )
        if response.status_code >= 400:
            raise ProviderError(
                f"Google Vision OCR request failed with status {response.status_code}.",
                provider_name=self.provider_name,
                context={"status_code": response.status_code},
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(
                "Google Vision OCR returned malformed JSON.",
                provider_name=self.provider_name,
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("responses"), list):
            raise ProviderError(
                "Google Vision OCR returned a malformed response payload.",
                provider_name=self.provider_name,
            )
        responses = payload["responses"]
        if len(responses) != 1 or not isinstance(responses[0], dict):
            raise ProviderError(
                "Google Vision OCR returned an unexpected response count.",
                provider_name=self.provider_name,
            )
        annotation = responses[0]
        error = annotation.get("error")
        if error:
            self._raise_annotation_error(error)
        return annotation

    def _raise_annotation_error(self, error: object) -> None:
        if not isinstance(error, dict):
            raise ProviderError(
                "Google Vision OCR returned a malformed error payload.",
                provider_name=self.provider_name,
            )
        code = error.get("code")
        if code in {401, 403}:
            raise ProviderAuthenticationError(
                "Google Vision OCR rejected the configured credentials.",
                provider_name=self.provider_name,
            )
        if code == 429:
            raise ProviderRateLimitError(
                "Google Vision OCR rate limit exceeded.",
                provider_name=self.provider_name,
            )
        if isinstance(code, int) and code >= 500:
            raise ProviderUnavailableError(
                "Google Vision OCR is temporarily unavailable.",
                provider_name=self.provider_name,
            )
        raise ProviderError(
            "Google Vision OCR rejected the image annotation request.",
            provider_name=self.provider_name,
            context={"status_code": code},
        )


def _backoff_seconds(attempt: int) -> float:
    return min(0.25 * (2 ** (attempt - 1)), 2.0)


def _mean_block_confidence(full_text: dict[str, Any]) -> float:
    confidences: list[float] = []
    pages = full_text.get("pages", [])
    if not isinstance(pages, list):
        return 0.0
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("blocks", []), list):
            continue
        for block in page.get("blocks", []):
            if not isinstance(block, dict):
                continue
            value = block.get("confidence")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                confidences.append(max(0.0, min(float(value), 1.0)))
    if not confidences:
        return 0.0
    return round(sum(confidences) / len(confidences), 4)
