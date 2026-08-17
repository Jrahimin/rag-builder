"""Google Cloud Vision implementation of the OCR provider contract."""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.platform.domain.text_normalizer import normalize_for_storage
from app.platform.providers.contracts.ocr import (
    OcrBlock,
    OcrBoundingBox,
    OcrImageInput,
    OcrPageResult,
    OcrParagraph,
    OCRProvider,
    OcrWord,
)
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
_GOOGLE_RPC_DEADLINE_EXCEEDED = 4
_GOOGLE_RPC_PERMISSION_DENIED = 7
_GOOGLE_RPC_RESOURCE_EXHAUSTED = 8
_GOOGLE_RPC_UNAVAILABLE = 14
_GOOGLE_RPC_UNAUTHENTICATED = 16


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
                    headers={"x-goog-api-key": self._api_key},
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
            blocks=_layout_blocks(full_text),
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
        if code in {_GOOGLE_RPC_PERMISSION_DENIED, _GOOGLE_RPC_UNAUTHENTICATED}:
            raise ProviderAuthenticationError(
                "Google Vision OCR rejected the configured credentials.",
                provider_name=self.provider_name,
            )
        if code == _GOOGLE_RPC_RESOURCE_EXHAUSTED:
            raise ProviderRateLimitError(
                "Google Vision OCR rate limit exceeded.",
                provider_name=self.provider_name,
            )
        if code == _GOOGLE_RPC_DEADLINE_EXCEEDED:
            raise ProviderTimeoutError(
                "Google Vision OCR request timed out.",
                provider_name=self.provider_name,
            )
        if code == _GOOGLE_RPC_UNAVAILABLE:
            raise ProviderUnavailableError(
                "Google Vision OCR is temporarily unavailable.",
                provider_name=self.provider_name,
            )
        raise ProviderError(
            "Google Vision OCR rejected the image annotation request.",
            provider_name=self.provider_name,
            context={"google_rpc_status_code": code},
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


def _layout_blocks(full_text: object) -> tuple[OcrBlock, ...]:
    """Map Vision layout into provider-neutral, normalized geometry."""
    if not isinstance(full_text, dict):
        return ()
    output: list[OcrBlock] = []
    pages = full_text.get("pages", [])
    if not isinstance(pages, list):
        return ()
    for page in pages:
        if not isinstance(page, dict):
            continue
        width = _positive_number(page.get("width"))
        height = _positive_number(page.get("height"))
        raw_blocks = page.get("blocks", [])
        if not isinstance(raw_blocks, list):
            continue
        for raw_block in raw_blocks:
            if not isinstance(raw_block, dict):
                continue
            paragraphs = _layout_paragraphs(raw_block, width=width, height=height)
            text = "\n".join(paragraph.text for paragraph in paragraphs if paragraph.text)
            if not text:
                continue
            output.append(
                OcrBlock(
                    text=text,
                    paragraphs=paragraphs,
                    bounding_box=_normalized_box(
                        raw_block.get("boundingBox"),
                        width=width,
                        height=height,
                    ),
                    confidence=_optional_confidence(raw_block.get("confidence")),
                )
            )
    return tuple(output)


def _layout_paragraphs(
    raw_block: dict[str, Any],
    *,
    width: float | None,
    height: float | None,
) -> tuple[OcrParagraph, ...]:
    raw_paragraphs = raw_block.get("paragraphs", [])
    if not isinstance(raw_paragraphs, list):
        return ()
    output: list[OcrParagraph] = []
    for raw_paragraph in raw_paragraphs:
        if not isinstance(raw_paragraph, dict):
            continue
        words = _layout_words(raw_paragraph, width=width, height=height)
        text = " ".join(word.text for word in words if word.text)
        if not text:
            continue
        output.append(
            OcrParagraph(
                text=normalize_for_storage(text),
                words=words,
                bounding_box=_normalized_box(
                    raw_paragraph.get("boundingBox"),
                    width=width,
                    height=height,
                ),
                confidence=_optional_confidence(raw_paragraph.get("confidence")),
            )
        )
    return tuple(output)


def _layout_words(
    raw_paragraph: dict[str, Any],
    *,
    width: float | None,
    height: float | None,
) -> tuple[OcrWord, ...]:
    raw_words = raw_paragraph.get("words", [])
    if not isinstance(raw_words, list):
        return ()
    output: list[OcrWord] = []
    for raw_word in raw_words:
        if not isinstance(raw_word, dict):
            continue
        symbols = raw_word.get("symbols", [])
        text = "".join(
            str(symbol.get("text", ""))
            for symbol in symbols
            if isinstance(symbol, dict)
        )
        normalized = normalize_for_storage(text)
        if not normalized:
            continue
        output.append(
            OcrWord(
                text=normalized,
                bounding_box=_normalized_box(
                    raw_word.get("boundingBox"),
                    width=width,
                    height=height,
                ),
                confidence=_optional_confidence(raw_word.get("confidence")),
            )
        )
    return tuple(output)


def _normalized_box(
    raw_box: object,
    *,
    width: float | None,
    height: float | None,
) -> OcrBoundingBox | None:
    if not isinstance(raw_box, dict):
        return None
    normalized_vertices = raw_box.get("normalizedVertices")
    if isinstance(normalized_vertices, list):
        points = _points(normalized_vertices, x_scale=1.0, y_scale=1.0)
    else:
        vertices = raw_box.get("vertices")
        if width is None or height is None or not isinstance(vertices, list):
            return None
        points = _points(vertices, x_scale=width, y_scale=height)
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return OcrBoundingBox(
        x_min=max(0.0, min(xs)),
        y_min=max(0.0, min(ys)),
        x_max=min(1.0, max(xs)),
        y_max=min(1.0, max(ys)),
    )


def _points(
    vertices: list[object],
    *,
    x_scale: float,
    y_scale: float,
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for vertex in vertices:
        if not isinstance(vertex, dict):
            continue
        x = vertex.get("x", 0)
        y = vertex.get("y", 0)
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            continue
        points.append((float(x) / x_scale, float(y) / y_scale))
    return points


def _positive_number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return None


def _optional_confidence(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return max(0.0, min(float(value), 1.0))
