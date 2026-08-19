"""Unit tests for the Google Cloud Vision OCR adapter."""

from __future__ import annotations

import json

import httpx
import pytest

from app.platform.providers.contracts.ocr import OcrImageInput
from app.platform.providers.errors import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.platform.providers.implementations.google_vision_ocr_provider import (
    GoogleVisionOCRProvider,
)

pytestmark = pytest.mark.unit


def _provider(
    handler: httpx.MockTransport,
    *,
    max_attempts: int = 3,
    sleeps: list[float] | None = None,
) -> GoogleVisionOCRProvider:
    return GoogleVisionOCRProvider(
        api_key="test-key",
        endpoint="https://vision.googleapis.com/v1/images:annotate",
        timeout_seconds=1.0,
        max_attempts=max_attempts,
        client=httpx.Client(transport=handler),
        sleep=(sleeps if sleeps is not None else []).append,
    )


def test_google_vision_success_normalizes_text_and_averages_block_confidence() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.params == httpx.QueryParams()
        assert request.headers["x-goog-api-key"] == "test-key"
        assert body["requests"][0]["features"] == [{"type": "DOCUMENT_TEXT_DETECTION"}]
        assert "imageContext" not in body["requests"][0]
        return httpx.Response(
            200,
            json={
                "responses": [
                    {
                        "fullTextAnnotation": {
                            "text": "বাংলা লেখা\nEnglish text",
                            "pages": [
                                {
                                    "blocks": [
                                        {"confidence": 0.8},
                                        {"confidence": 1.0},
                                    ]
                                }
                            ],
                        }
                    }
                ]
            },
        )

    result = _provider(httpx.MockTransport(respond)).recognize(
        OcrImageInput(data=b"image", mime_type="image/png", page_number=2)
    )

    assert result.text == "বাংলা লেখা English text"
    assert result.lines == ("বাংলা লেখা", "English text")
    assert result.confidence == 0.9
    assert result.page_number == 2


def test_google_vision_preserves_normalized_word_geometry() -> None:
    def word(text: str, x1: int, y1: int, x2: int, y2: int) -> dict:
        return {
            "symbols": [{"text": character} for character in text],
            "boundingBox": {
                "vertices": [
                    {"x": x1, "y": y1},
                    {"x": x2, "y": y1},
                    {"x": x2, "y": y2},
                    {"x": x1, "y": y2},
                ]
            },
            "confidence": 0.91,
        }

    provider = _provider(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "responses": [
                        {
                            "fullTextAnnotation": {
                                "text": "Head Value",
                                "pages": [
                                    {
                                        "width": 1000,
                                        "height": 2000,
                                        "blocks": [
                                            {
                                                "confidence": 0.9,
                                                "paragraphs": [
                                                    {
                                                        "words": [
                                                            word("Head", 100, 200, 200, 240),
                                                            word("Value", 600, 200, 750, 240),
                                                        ]
                                                    }
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        )
    )

    result = provider.recognize(OcrImageInput(data=b"image", page_number=1))

    assert result.blocks[0].paragraphs[0].words[0].text == "Head"
    box = result.blocks[0].paragraphs[0].words[1].bounding_box
    assert box is not None
    assert box.x_min == 0.6
    assert box.y_max == 0.12


def test_google_vision_maps_authentication_failure() -> None:
    provider = _provider(
        httpx.MockTransport(lambda _request: httpx.Response(403, json={"error": "denied"}))
    )
    with pytest.raises(ProviderAuthenticationError):
        provider.recognize(OcrImageInput(data=b"image"))


@pytest.mark.parametrize(
    ("code", "error_type", "retryable"),
    [
        (7, ProviderAuthenticationError, False),
        (16, ProviderAuthenticationError, False),
        (8, ProviderRateLimitError, True),
        (4, ProviderTimeoutError, True),
        (14, ProviderUnavailableError, True),
    ],
)
def test_google_vision_maps_annotation_google_rpc_status_codes(
    code: int,
    error_type: type[ProviderError],
    retryable: bool,
) -> None:
    provider = _provider(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"responses": [{"error": {"code": code}}]},
            )
        ),
        max_attempts=1,
    )

    with pytest.raises(error_type) as exc_info:
        provider.recognize(OcrImageInput(data=b"image"))

    assert exc_info.value.retryable is retryable


def test_google_vision_retries_rate_limit_then_succeeds() -> None:
    calls = 0
    sleeps: list[float] = []

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": "limited"})
        return httpx.Response(200, json={"responses": [{}]})

    result = _provider(httpx.MockTransport(respond), sleeps=sleeps).recognize(
        OcrImageInput(data=b"image")
    )

    assert result.text == ""
    assert calls == 2
    assert sleeps == [0.25]


def test_google_vision_rejects_malformed_payload() -> None:
    provider = _provider(
        httpx.MockTransport(lambda _request: httpx.Response(200, json={"unexpected": []}))
    )
    with pytest.raises(ProviderError, match="malformed response"):
        provider.recognize(OcrImageInput(data=b"image"))


def test_google_vision_timeout_is_retryable_after_bounded_attempts() -> None:
    calls = 0
    sleeps: list[float] = []

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out", request=request)

    provider = _provider(httpx.MockTransport(timeout), max_attempts=2, sleeps=sleeps)

    with pytest.raises(ProviderTimeoutError) as exc_info:
        provider.recognize(OcrImageInput(data=b"image"))

    assert exc_info.value.retryable is True
    assert calls == 2
    assert sleeps == [0.25]
