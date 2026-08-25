"""Request and response contracts for contextual generation."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from app.core.config import GenerationRetentionMode, LLMBackend
from app.models.generation import Generation, GenerationStatus, GroundingStatus

_LOCALE_PATTERN = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


class GenerationRequestConfig(BaseModel):
    """Deprecated compatibility fields; Project policy owns these values."""

    model_config = ConfigDict(extra="forbid")

    provider: LLMBackend | None = Field(default=None, deprecated=True)
    model: Annotated[str | None, Field(min_length=1, max_length=128, deprecated=True)] = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0, deprecated=True)
    max_tokens: int | None = Field(default=None, ge=1, le=128_000, deprecated=True)


class GenerationCreateRequest(BaseModel):
    """Trusted caller input and context for one registered generation use case."""

    model_config = ConfigDict(extra="forbid")

    use_case: Annotated[
        str,
        Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.-]*$"),
    ]
    input: JsonValue
    context: JsonValue
    prompt_version: Annotated[str | None, Field(min_length=1, max_length=64, deprecated=True)] = (
        None
    )
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
    configuration_hash: str | None
    config_provenance: dict[str, object]
    index_build_id: str | None
    source_metadata_generation: int | None
    source_provenance: Literal["none"] = "none"
    context_provenance: Literal["caller_context"] = "caller_context"
    web_enrichment_used: bool = False
    resolved_chat_response_mode: str = "indexed_only"
    created_at: datetime
    completed_at: datetime | None

    @classmethod
    def from_generation(
        cls,
        generation: Generation,
        *,
        idempotency_replayed: bool = False,
    ) -> GenerationResponse:
        total_tokens = (
            generation.input_tokens + generation.output_tokens
            if generation.input_tokens is not None and generation.output_tokens is not None
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
            configuration_hash=generation.configuration_hash,
            config_provenance=dict(generation.config_provenance),
            index_build_id=(str(generation.index_build_id) if generation.index_build_id else None),
            source_metadata_generation=generation.source_metadata_generation,
            source_provenance="none",
            context_provenance="caller_context",
            web_enrichment_used=False,
            resolved_chat_response_mode=_resolved_chat_response_mode(generation),
            created_at=generation.created_at,
            completed_at=generation.completed_at,
        )


def _resolved_chat_response_mode(generation: Generation) -> str:
    configuration = generation.config_snapshot.get("configuration")
    if not isinstance(configuration, dict):
        return "indexed_only"
    chat = configuration.get("chat")
    if not isinstance(chat, dict):
        return "indexed_only"
    value = chat.get("response_mode")
    return str(value) if value is not None else "indexed_only"
