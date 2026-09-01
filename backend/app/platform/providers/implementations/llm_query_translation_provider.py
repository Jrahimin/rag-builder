"""LLM-backed retrieval translator behind BaseLLMProvider."""

from __future__ import annotations

import json
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

# Page-length query + buffer. Rejects rambling, not a book-page translation.
_MAX_TRANSLATION_CHARS = 24_000
_DEFAULT_MIN_TRANSLATION_OUTPUT_TOKENS = 256
_MAX_TRANSLATION_OUTPUT_TOKENS = 2048
_SHORT_QUERY_CLEAN_CHARS = 400
_ABSOLUTE_MIN_TRANSLATION_OUTPUT_TOKENS = 16


class LLMQueryTranslationProvider(BaseQueryTranslationProvider):
    """Adapter that never lets retrieval import a vendor LLM SDK."""

    def __init__(
        self,
        llm: BaseLLMProvider,
        *,
        prompt_version: str = PROMPT_VERSION,
        min_output_tokens: int = _DEFAULT_MIN_TRANSLATION_OUTPUT_TOKENS,
        max_output_tokens: int = 4096,
        temperature: float | None = None,
        retry_max_attempts: int = 1,
    ) -> None:
        self._llm = llm
        self._prompt_version = prompt_version
        self._min_output_tokens = min_output_tokens
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._retry_max_attempts = max(0, retry_max_attempts)

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

    def _max_tokens(self, request: QueryTranslationRequest) -> int:
        # Bangla can approach ~1 token per 1-2 chars; English is cheaper.
        # A short-query floor is a starvation hypothesis, not a proven root cause.
        configured_min = max(_ABSOLUTE_MIN_TRANSLATION_OUTPUT_TOKENS, self._min_output_tokens)
        ceiling = min(
            request.max_output_tokens,
            self._max_output_tokens,
            _MAX_TRANSLATION_OUTPUT_TOKENS,
        )
        floor = min(configured_min, ceiling)
        sized = max(floor, min(_MAX_TRANSLATION_OUTPUT_TOKENS, (len(request.query) // 2) + 96))
        return min(ceiling, sized)

    async def translate(self, request: QueryTranslationRequest) -> QueryTranslationResponse:
        payload = translation_messages(
            query=request.query,
            target_language=request.target_language,
            source_profile=request.source_profile,
            prompt_version=request.prompt_version or self._prompt_version,
        )
        messages = [
            ChatMessage(role=ChatRole(item["role"]), content=item["content"]) for item in payload
        ]
        started = time.perf_counter()
        attempts = 1 + self._retry_max_attempts
        completion = None
        translated = ""
        error: str | None = "empty"
        validation_reasons: list[str] = []
        attempt_count = 0
        for _ in range(attempts):
            attempt_count += 1
            try:
                completion = await self._llm.generate(
                    messages,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens(request),
                )
            except ProviderError as exc:
                context = dict(exc.context) if isinstance(exc.context, dict) else {}
                context.update(
                    {
                        "attempts": attempt_count,
                        "provider": exc.provider_name or self.provider_name,
                        "model": self.model_name,
                        "prompt_version": request.prompt_version or self._prompt_version,
                        "latency_ms": int((time.perf_counter() - started) * 1000),
                    }
                )
                raise ProviderError(
                    exc.message,
                    provider_name=exc.provider_name or self.provider_name,
                    retryable=exc.retryable,
                    context=context,
                ) from exc
            translated = _clean_translation(completion.content)
            error = _validation_error(request.query, translated)
            if error is None:
                break
            validation_reasons.append(error)
        latency_ms = int((time.perf_counter() - started) * 1000)
        if completion is None or error is not None:
            diagnostic_reason = next(
                (reason for reason in reversed(validation_reasons) if reason != "empty"),
                error or "empty",
            )
            failure_context: dict[str, object] = {
                "reason": diagnostic_reason,
                "validation_reasons": validation_reasons,
                "attempts": attempt_count,
                "provider": self.provider_name,
                "model": self.model_name,
                "prompt_version": request.prompt_version or self._prompt_version,
                "latency_ms": latency_ms,
            }
            if completion is not None:
                failure_context.update(_completion_diagnostics(completion))
            raise ProviderError(
                "Retrieval translation failed validation.",
                provider_name=self.provider_name,
                context=failure_context,
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
                reasoning_tokens=getattr(completion.usage, "reasoning_tokens", None),
            ),
            latency_ms=latency_ms,
            attempts=attempt_count,
        )


def _completion_diagnostics(completion: object) -> dict[str, object]:
    """Record empty/validation-failure signals without assuming token starvation."""
    diagnostics: dict[str, object] = {}
    finish_reason = getattr(completion, "finish_reason", None)
    if finish_reason is not None:
        diagnostics["finish_reason"] = str(finish_reason)
    usage = getattr(completion, "usage", None)
    if usage is None:
        return diagnostics
    output_tokens = getattr(usage, "output_tokens", None)
    if output_tokens is not None:
        diagnostics["output_tokens"] = int(output_tokens)
    reasoning_tokens = getattr(usage, "reasoning_tokens", None)
    if reasoning_tokens is not None:
        diagnostics["reasoning_tokens"] = int(reasoning_tokens)
    return diagnostics


def _clean_translation(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""
    cleaned = _unwrap_fence(cleaned)
    cleaned = _unwrap_json_query(cleaned)
    cleaned = _prefer_translation_text(cleaned)
    cleaned = _unwrap_matching_quotes(cleaned)
    return " ".join(cleaned.split())


def _prefer_translation_text(text: str) -> str:
    """Keep page-length translations; drop a trailing explanation on short queries."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return text.strip()
    first = _strip_translation_label(lines[0])
    rest = lines[1:]
    if not first and rest:
        first = _strip_translation_label(rest[0])
        rest = rest[1:]
    if not rest:
        return first
    joined = " ".join([first, *rest])
    if len(joined) <= _SHORT_QUERY_CLEAN_CHARS:
        return first
    return joined


def _strip_translation_label(text: str) -> str:
    for prefix in ("translation:", "translated query:", "query:"):
        lowered = text.casefold()
        if lowered.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def _unwrap_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    body = text[3:]
    newline = body.find("\n")
    if newline != -1:
        language = body[:newline].strip()
        if language and " " not in language:
            body = body[newline + 1 :]
    if body.endswith("```"):
        body = body[:-3]
    return body.strip()


def _unwrap_json_query(text: str) -> str:
    candidate = text
    if not (candidate.startswith("{") and candidate.endswith("}")):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return text
        candidate = text[start : end + 1]
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return text
    if not isinstance(payload, dict):
        return text
    for key in ("translated_query", "translation", "query", "text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return text


def _unwrap_matching_quotes(text: str) -> str:
    if len(text) < 2:
        return text
    pairs = {('"', '"'), ("'", "'"), ("\u201c", "\u201d"), ("\u2018", "\u2019")}
    if (text[0], text[-1]) in pairs:
        return text[1:-1].strip()
    return text


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
