"""LLM-backed retrieval translator behind BaseLLMProvider."""

from __future__ import annotations

import time

from app.platform.domain.language_detection import missing_protected_literals
from app.platform.providers.contracts.llm import BaseLLMProvider, ChatMessage, ChatRole
from app.platform.providers.contracts.query_translation import (
    BaseQueryTranslationProvider,
    QueryTranslationRequest,
    QueryTranslationResponse,
    QueryTranslationUsage,
)
from app.platform.providers.errors import ProviderError
from app.platform.providers.prompts.retrieval_translation import (
    PROMPT_VERSION,
    translation_messages,
)

_MAX_TRANSLATION_CHARS = 2000


class LLMQueryTranslationProvider(BaseQueryTranslationProvider):
    """Adapter that never lets retrieval import a vendor LLM SDK."""

    def __init__(
        self,
        llm: BaseLLMProvider,
        *,
        prompt_version: str = PROMPT_VERSION,
        max_output_tokens: int = 256,
        temperature: float | None = None,
    ) -> None:
        self._llm = llm
        self._prompt_version = prompt_version
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature

    @property
    def provider_name(self) -> str:
        return self._llm.provider_name

    @property
    def model_name(self) -> str:
        return self._llm.model_name

    @property
    def provider_version(self) -> str:
        return self._llm.provider_version

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    async def translate(self, request: QueryTranslationRequest) -> QueryTranslationResponse:
        payload = translation_messages(
            query=request.query,
            target_language=request.target_language,
            source_profile=request.source_profile,
        )
        messages = [
            ChatMessage(role=ChatRole(item["role"]), content=item["content"]) for item in payload
        ]
        started = time.perf_counter()
        completion = await self._llm.generate(
            messages,
            temperature=self._temperature,
            max_tokens=min(request.max_output_tokens, self._max_output_tokens),
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        translated = _clean_translation(completion.content)
        error = _validation_error(request.query, translated)
        if error is not None:
            raise ProviderError(
                "Retrieval translation failed validation.",
                provider_name=self.provider_name,
                context={"reason": error},
            )
        return QueryTranslationResponse(
            translated_query=translated,
            provider=completion.provider,
            model=completion.model,
            provider_version=completion.provider_version,
            prompt_version=self._prompt_version,
            usage=QueryTranslationUsage(
                input_tokens=completion.usage.input_tokens,
                output_tokens=completion.usage.output_tokens,
            ),
            latency_ms=latency_ms,
        )


def _clean_translation(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith(("```", '"', "'")) and cleaned.endswith(("```", '"', "'")):
        cleaned = cleaned.strip("`").strip().strip('"').strip("'")
    return " ".join(cleaned.split())


def _validation_error(original: str, translated: str) -> str | None:
    if not translated:
        return "empty"
    if len(translated) > _MAX_TRANSLATION_CHARS:
        return "too_long"
    if len(translated) > max(64, len(original) * 8):
        return "too_long"
    missing = missing_protected_literals(original, translated)
    if missing:
        return "missing_literals"
    return None
