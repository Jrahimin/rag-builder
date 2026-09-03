"""Synchronous contextual generation orchestration and trace persistence."""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import GenerationConfig, LLMConfig, Settings
from app.core.exceptions import ConflictError, NotFoundError, ServiceUnavailableError
from app.models.generation import Generation, GenerationStatus, GroundingStatus
from app.modules.generation.errors import GenerationOutputValidationError
from app.modules.generation.prompts.registry import resolve_generation_spec
from app.modules.generation.repositories.generation_repository import GenerationRepository
from app.modules.generation.schemas.generation import (
    GenerationCreateRequest,
    GenerationResponse,
)
from app.modules.generation.services.generation_prompt_builder import GenerationPromptBuilder
from app.modules.generation.services.output_validation_service import OutputValidationService
from app.modules.generation.services.payload_validation_service import (
    PayloadValidationService,
    sha256_json,
)
from app.platform.config.project_ai import ConfigRevisionRecord, resolve_project_ai_config
from app.platform.domain.transactions import commit_refresh
from app.platform.providers.contracts.llm import BaseLLMProvider, ChatCompletionResult
from app.platform.providers.errors import ProviderError

logger = structlog.get_logger(__name__)

type LLMResolver = Callable[[str | None, str | None], BaseLLMProvider]


class GenerationService:
    """Validate → resolve → generate → validate output → persist trace."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        project_id: uuid.UUID,
        repository: GenerationRepository,
        generation_config: GenerationConfig,
        llm_config: LLMConfig,
        resolve_llm: LLMResolver,
        settings: Settings | None = None,
        active_revision: ConfigRevisionRecord | None = None,
        execution_provenance: dict[str, object] | None = None,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._repository = repository
        self._generation_config = generation_config
        self._llm_config = llm_config
        self._resolve_llm = resolve_llm
        self._settings = settings or Settings(llm=llm_config, generation=generation_config)
        self._active_revision = active_revision
        self._execution_provenance = execution_provenance or {}
        self._payloads = PayloadValidationService(generation_config)
        self._outputs = OutputValidationService()
        self._prompts = GenerationPromptBuilder()

    async def create(
        self,
        request: GenerationCreateRequest,
        *,
        idempotency_key: str | None,
        request_id: str,
        trace_id: str,
    ) -> GenerationResponse:
        started = time.perf_counter()
        payload = self._payloads.validate(request)
        request_config = request.generation_config
        explicit_generation_fields = request_config.model_fields_set
        deprecated_overrides = {
            field: (
                getattr(request_config, field).value
                if field == "provider" and getattr(request_config, field) is not None
                else getattr(request_config, field)
            )
            for field in explicit_generation_fields
        }
        # Prompt-version selection remains part of the contextual generation
        # contract until the dedicated prompt consolidation phase.
        prompt_version = request.prompt_version
        resolution = resolve_project_ai_config(
            self._settings,
            self._active_revision,
            deprecated_overrides=deprecated_overrides,
            # Contextual generation is caller-context-only; chat web-search readiness and
            # source-aware chat prompt requirements must not govern this workload.
            validate_chat_response_policy=False,
        )
        spec = resolve_generation_spec(
            use_case=request.use_case,
            prompt_version=prompt_version,
            response_schema=(
                dict(request.response_schema) if request.response_schema is not None else None
            ),
        )
        self._payloads.validate_schema_size(spec.response_schema)
        self._outputs.validate_schema(spec.response_schema)

        provider = resolution.configuration.llm.provider.value
        model = resolution.configuration.llm.model
        temperature = resolution.configuration.llm.temperature
        max_tokens = resolution.configuration.llm.max_tokens
        provenance = {
            **resolution.provenance.model_dump(mode="json"),
            **self._execution_provenance,
        }
        provenance["prompt_versions"] = {
            **provenance["prompt_versions"],
            "generation": spec.prompt.prompt_version,
            "generation_schema": spec.schema_version,
        }
        provenance["contextual_generation_policy"] = {
            "context_authority": "caller_context",
            "web_enrichment_allowed": False,
            "web_enrichment_used": False,
            "source_provenance": "none",
        }
        request_hash = sha256_json(
            {
                "use_case": request.use_case,
                "input": request.input,
                "context": request.context,
                "prompt_version": spec.prompt.prompt_version,
                "response_schema": spec.response_schema,
                "locale": request.locale,
                "provider": provider,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "configuration_hash": resolution.configuration_hash,
                "domain_instructions": resolution.configuration.domain_instructions,
                "prompt_profile": resolution.configuration.prompt_profile,
                "retention": payload.retention.value,
            }
        )
        messages = self._prompts.build(
            spec=spec,
            canonical_input=payload.canonical_input,
            canonical_context=payload.canonical_context,
            locale=request.locale,
            domain_instructions=resolution.configuration.domain_instructions,
            prompt_profile=resolution.configuration.prompt_profile,
        )

        idempotency_hash = (
            hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
            if idempotency_key is not None
            else None
        )
        generation = Generation(
            project_id=self._project_id,
            use_case=request.use_case,
            status=GenerationStatus.PROCESSING,
            grounding_status=GroundingStatus.CONTEXT_SUPPLIED,
            grounded=False,
            idempotency_key_hash=idempotency_hash,
            request_hash=request_hash,
            prompt_version=spec.prompt.prompt_version,
            schema_version=spec.schema_version,
            response_schema=spec.response_schema,
            locale=request.locale,
            provider=provider,
            model=model,
            generation_config={
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            configuration_hash=resolution.configuration_hash,
            index_build_id=(
                uuid.UUID(str(self._execution_provenance["active_index_build_id"]))
                if self._execution_provenance.get("active_index_build_id")
                else None
            ),
            source_metadata_generation=_optional_int(
                self._execution_provenance.get("source_metadata_generation")
            ),
            config_snapshot={
                **resolution.secret_free_snapshot(),
                "workload": "contextual_generation",
                "prompt_version": spec.prompt.prompt_version,
                "schema_version": spec.schema_version,
                "execution_provenance": self._execution_provenance,
                "contextual_generation_policy": provenance["contextual_generation_policy"],
            },
            config_provenance=provenance,
            retention_mode=payload.retention,
            retained_input=payload.retained_input,
            retained_context=payload.retained_context,
            payload_metadata=payload.metadata,
            request_id=request_id,
            trace_id=trace_id,
        )
        generation, replayed = await self._reserve(generation)
        if replayed:
            return GenerationResponse.from_generation(
                generation,
                idempotency_replayed=True,
            )

        try:
            llm = self._resolve_llm(provider, model)
        except ProviderError as exc:
            await self._persist_provider_failure(
                generation,
                exc,
                total_ms=self._elapsed_ms(started),
            )
            raise self._provider_unavailable(exc) from exc

        provider_started = time.perf_counter()
        try:
            completion = await llm.generate(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except ProviderError as exc:
            await self._persist_provider_failure(
                generation,
                exc,
                provider_ms=self._elapsed_ms(provider_started),
                total_ms=self._elapsed_ms(started),
            )
            raise self._provider_unavailable(exc) from exc

        provider_ms = self._elapsed_ms(provider_started)
        try:
            output = self._outputs.parse_and_validate(
                completion.content,
                spec.response_schema,
            )
        except GenerationOutputValidationError:
            await self._persist_output_failure(
                generation,
                completion=completion,
                provider_ms=provider_ms,
                total_ms=self._elapsed_ms(started),
            )
            raise

        generation.status = GenerationStatus.SUCCEEDED
        generation.grounded = True
        generation.output = output
        generation.provider = completion.provider
        generation.model = completion.model
        generation.provider_version = completion.provider_version
        generation.finish_reason = completion.finish_reason
        generation.input_tokens = completion.usage.input_tokens
        generation.output_tokens = completion.usage.output_tokens
        generation.provider_latency_ms = provider_ms
        generation.total_latency_ms = self._elapsed_ms(started)
        generation.completed_at = datetime.now(UTC)
        await commit_refresh(self._session, generation)

        logger.info(
            "contextual_generation_complete",
            project_id=str(self._project_id),
            generation_id=str(generation.id),
            use_case=generation.use_case,
            provider=generation.provider,
            model=generation.model,
            prompt_version=generation.prompt_version,
            schema_version=generation.schema_version,
            input_tokens=generation.input_tokens,
            output_tokens=generation.output_tokens,
            provider_latency_ms=generation.provider_latency_ms,
            total_latency_ms=generation.total_latency_ms,
            retention=generation.retention_mode.value,
        )
        return GenerationResponse.from_generation(generation)

    async def get(self, generation_id: uuid.UUID) -> GenerationResponse:
        generation = await self._repository.get_by_id(generation_id)
        if generation is None:
            raise NotFoundError(
                message="Generation not found.",
                code="generation_not_found",
            )
        return GenerationResponse.from_generation(generation)

    async def _reserve(self, generation: Generation) -> tuple[Generation, bool]:
        if generation.idempotency_key_hash is not None:
            existing = await self._repository.get_by_idempotency_key_hash(
                generation.idempotency_key_hash
            )
            if existing is not None:
                return self._handle_existing(existing, generation.request_hash)

        self._repository.add(generation)
        try:
            return await commit_refresh(self._session, generation), False
        except IntegrityError:
            await self._session.rollback()
            if generation.idempotency_key_hash is None:
                raise
            existing = await self._repository.get_by_idempotency_key_hash(
                generation.idempotency_key_hash
            )
            if existing is None:
                raise
            return self._handle_existing(existing, generation.request_hash)

    def _handle_existing(
        self,
        existing: Generation,
        request_hash: str,
    ) -> tuple[Generation, bool]:
        if existing.request_hash != request_hash:
            raise ConflictError(
                message="The idempotency key was already used with a different request.",
                code="generation_idempotency_conflict",
            )
        if existing.status is GenerationStatus.SUCCEEDED:
            return existing, True
        if existing.status is GenerationStatus.PROCESSING:
            raise ConflictError(
                message="A generation with this idempotency key is still processing.",
                code="generation_in_progress",
            )
        if existing.error_code == "generation_output_schema_mismatch":
            raise GenerationOutputValidationError()
        raise ServiceUnavailableError(
            message="The prior idempotent generation attempt failed.",
            code=existing.error_code or "generation_failed",
        )

    async def _persist_provider_failure(
        self,
        generation: Generation,
        exc: ProviderError,
        *,
        provider_ms: int | None = None,
        total_ms: int,
    ) -> None:
        generation.status = GenerationStatus.FAILED
        generation.grounding_status = GroundingStatus.FAILED
        generation.error_code = "llm_provider_unavailable"
        generation.error_message = "The language model provider is temporarily unavailable."
        generation.provider_latency_ms = provider_ms
        generation.total_latency_ms = total_ms
        generation.completed_at = datetime.now(UTC)
        await commit_refresh(self._session, generation)
        logger.warning(
            "contextual_generation_failed",
            project_id=str(self._project_id),
            generation_id=str(generation.id),
            provider=exc.provider_name,
            error_code=generation.error_code,
            error=str(exc),
        )

    async def _persist_output_failure(
        self,
        generation: Generation,
        *,
        completion: ChatCompletionResult,
        provider_ms: int,
        total_ms: int,
    ) -> None:
        generation.status = GenerationStatus.FAILED
        generation.grounding_status = GroundingStatus.FAILED
        generation.provider = completion.provider
        generation.model = completion.model
        generation.provider_version = completion.provider_version
        generation.finish_reason = completion.finish_reason
        generation.input_tokens = completion.usage.input_tokens
        generation.output_tokens = completion.usage.output_tokens
        generation.provider_latency_ms = provider_ms
        generation.total_latency_ms = total_ms
        generation.error_code = "generation_output_schema_mismatch"
        generation.error_message = (
            "The language model returned output that did not match the response schema."
        )
        generation.completed_at = datetime.now(UTC)
        await commit_refresh(self._session, generation)
        logger.warning(
            "contextual_generation_failed",
            project_id=str(self._project_id),
            generation_id=str(generation.id),
            provider=generation.provider,
            error_code=generation.error_code,
        )

    def _provider_unavailable(self, exc: ProviderError) -> ServiceUnavailableError:
        return ServiceUnavailableError(
            message="The language model provider is temporarily unavailable.",
            code="llm_provider_unavailable",
            context={"provider": exc.provider_name, "provider_error": str(exc)},
        )

    def _elapsed_ms(self, started: float) -> int:
        return int((time.perf_counter() - started) * 1000)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TypeError("Source generations must be integer-compatible values")
    return int(value)
