"""Robust retrieval-translation output parsing and empty fallback."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.platform.providers.contracts.query_translation import QueryTranslationRequest
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.llm_query_translation_provider import (
    LLMQueryTranslationProvider,
    _clean_translation,
)

pytestmark = pytest.mark.unit


class _FakeLLM:
    provider_name = "openai"
    model_name = "gpt-5-nano"
    provider_version = "test"

    def __init__(self, content: str) -> None:
        self._content = content

    async def generate(self, *_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(
            content=self._content,
            provider="openai",
            model="gpt-5-nano",
            provider_version="test",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )


def _request() -> QueryTranslationRequest:
    return QueryTranslationRequest(
        query="source tax deduction",
        source_profile="en",
        target_language="bn",
        prompt_version="retrieval-translation-v2",
        max_output_tokens=64,
    )


def test_clean_translation_unwraps_fences_json_and_quotes() -> None:
    assert _clean_translation(' "উৎসে কর" ') == "উৎসে কর"
    assert _clean_translation("```text\nউৎসে কর\n```") == "উৎসে কর"
    assert _clean_translation('{"translated_query": "উৎসে কর"}') == "উৎসে কর"
    assert _clean_translation('Here is the JSON:\n{"query": "উৎসে কর"}') == "উৎসে কর"
    assert _clean_translation("“উৎসে কর”") == "উৎসে কর"


async def test_empty_model_output_fails_closed_with_empty_reason() -> None:
    translator = LLMQueryTranslationProvider(_FakeLLM("   \n```\n```\n"))
    with pytest.raises(ProviderError) as caught:
        await translator.translate(_request())
    assert caught.value.context["reason"] == "empty"


async def test_wrapped_translation_is_accepted() -> None:
    translator = LLMQueryTranslationProvider(_FakeLLM('```json\n{"query": "উৎসে কর"}\n```'))
    result = await translator.translate(_request())
    assert result.translated_query == "উৎসে কর"


def test_clean_translation_strips_labels_and_uses_first_line() -> None:
    assert _clean_translation("Translation: উৎসে কর") == "উৎসে কর"
    assert _clean_translation("Translation:\nউৎসে কর") == "উৎসে কর"
    assert _clean_translation("উৎসে কর\nThis is an explanation.") == "উৎসে কর"


def test_clean_translation_keeps_page_length_multiline() -> None:
    paragraph = "সঞ্চয়পত্র হইতে অর্জিত মুনাফা সম্পত্তির অধিগ্রহণ। "
    page = (paragraph * 40).strip()
    assert len(page) > 400
    assert _clean_translation(page.replace("। ", "।\n")) == " ".join(page.split())


class _RecordingLLM:
    provider_name = "openai"
    model_name = "gpt-5-nano"
    provider_version = "test"

    def __init__(self, contents: list[str]) -> None:
        self._contents = list(contents)
        self.max_tokens: list[int] = []

    async def generate(self, *_args: object, **kwargs: object) -> object:
        self.max_tokens.append(int(kwargs["max_tokens"]))
        content = self._contents.pop(0) if self._contents else ""
        return SimpleNamespace(
            content=content,
            provider="openai",
            model="gpt-5-nano",
            provider_version="test",
            usage=SimpleNamespace(input_tokens=12, output_tokens=6),
        )


async def test_translation_caps_output_tokens_and_retries_empty() -> None:
    llm = _RecordingLLM(["", "উৎসে কর সংগ্রহ"])
    translator = LLMQueryTranslationProvider(llm, max_output_tokens=1024, retry_max_attempts=1)
    result = await translator.translate(
        QueryTranslationRequest(
            query="source tax deduction",
            source_profile="en",
            target_language="bn",
            prompt_version="retrieval-translation-v2",
            max_output_tokens=1024,
        )
    )
    assert result.translated_query == "উৎসে কর সংগ্রহ"
    assert llm.max_tokens == [266, 266]


async def test_page_length_query_gets_a_page_sized_output_budget() -> None:
    page = ("source tax categories include savings certificates. " * 80).strip()
    llm = _RecordingLLM(["সঞ্চয়পত্র হইতে অর্জিত মুনাফা"])
    translator = LLMQueryTranslationProvider(llm, max_output_tokens=4096)
    await translator.translate(
        QueryTranslationRequest(
            query=page,
            source_profile="en",
            target_language="bn",
            prompt_version="retrieval-translation-v2",
            max_output_tokens=4096,
        )
    )
    expected = min(4096, (len(page) // 2) + 256)
    assert llm.max_tokens == [expected]


async def test_non_empty_validation_failure_does_not_retry() -> None:
    llm = _RecordingLLM(["x" * 5000])
    translator = LLMQueryTranslationProvider(llm, retry_max_attempts=2)
    with pytest.raises(ProviderError) as caught:
        await translator.translate(_request())
    assert caught.value.context["reason"] == "too_long"
    assert len(llm.max_tokens) == 1
