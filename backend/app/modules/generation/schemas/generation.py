"""Request and response contracts for contextual generation."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from app.core.config import GenerationRetentionMode, LLMBackend
from app.models.generation import Generation, GenerationStatus, GroundingStatus

_LOCALE_PATTERN = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


class GenerationRequestConfig(BaseModel):
    """Bounded caller overrides for model generation."""

    model_config = ConfigDict(extra="forbid")

    provider: LLMBackend | None = None
    model: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=128_000)


class GenerationCreateRequest(BaseModel):
    """Trusted caller input and context for one registered generation use case."""

    model_config = ConfigDict(extra="forbid")

    use_case: Annotated[
        str,
        Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.-]*$"),
    ]
    input: JsonValue
    context: JsonValue
    prompt_version: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    response_schema: dict[str, JsonValue] | None = None
    locale: Annotated[str | None, Field(min_length=2, max_length=35)] = None
    generation_config: GenerationRequestConfig = Field(default_factory=GenerationRequestConfig)
    retention: GenerationRetentionMode | None = None

    @field_validator("context")
    @classmethod
    def _validate_context_root(cls, value: JsonValue) -> JsonValue:
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("context text must not be empty")
            return value
        if isinstance(value, (dict, list)):
            if not value:
                raise ValueError("context must not be empty")
            return value
        raise ValueError("context must be text, an object, or an array")

    @field_validator("locale")
    @classmethod
    def _validate_locale(cls, value: str | None) -> str | None:
        if value is not None and _LOCALE_PATTERN.fullmatch(value) is None:
            raise ValueError("locale must be a BCP 47 language tag")
        return value


class GenerationUsage(BaseModel):
    """Normalized token counts."""

    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


class GenerationTiming(BaseModel):
    """Provider and end-to-end duration."""

    provider_ms: int | None
    total_ms: int | None


class GenerationFailure(BaseModel):
    """Safe persisted terminal failure detail."""

    code: str
    message: str


class GenerationResponse(BaseModel):
    """Normalized generation result returned by create and get."""

    id: str
    project_id: str
    use_case: str
    status: GenerationStatus
    output: JsonValue | None
    grounded: bool
    grounding_status: GroundingStatus
    provider: str
    model: str
    provider_version: str | None
    prompt_version: str
    schema_version: str
    usage: GenerationUsage
    timing: GenerationTiming
    request_id: str
    trace_id: str
    retention: GenerationRetentionMode
    payload_retained: bool
    idempotency_replayed: bool = False
    finish_reason: str | None
    failure: GenerationFailure | None
    created_at: datetime
    completed_at: datetime | None

    @classmethod
    def from_generation(
        cls,
        generation: Generation,
        *,
        idempotency_replayed: bool = False,
    ) -> GenerationResponse:
        token_values = (generation.input_tokens, generation.output_tokens)
        total_tokens = (
            sum(value for value in token_values if value is not None)
            if any(value is not None for value in token_values)
            else None
        )
        failure = (
            GenerationFailure(
                code=generation.error_code,
                message=generation.error_message or "Generation failed.",
            )
            if generation.error_code is not None
            else None
        )
        return cls(
            id=str(generation.id),
            project_id=str(generation.project_id),
            use_case=generation.use_case,
            status=generation.status,
            output=generation.output,
            grounded=generation.grounded,
            grounding_status=generation.grounding_status,
            provider=generation.provider,
            model=generation.model,
            provider_version=generation.provider_version,
            prompt_version=generation.prompt_version,
            schema_version=generation.schema_version,
            usage=GenerationUsage(
                input_tokens=generation.input_tokens,
                output_tokens=generation.output_tokens,
                total_tokens=total_tokens,
            ),
            timing=GenerationTiming(
                provider_ms=generation.provider_latency_ms,
                total_ms=generation.total_latency_ms,
            ),
            request_id=generation.request_id,
            trace_id=generation.trace_id,
            retention=generation.retention_mode,
            payload_retained=(
                generation.retained_input is not None or generation.retained_context is not None
            ),
            idempotency_replayed=idempotency_replayed,
            finish_reason=generation.finish_reason,
            failure=failure,
            created_at=generation.created_at,
            completed_at=generation.completed_at,
        )
