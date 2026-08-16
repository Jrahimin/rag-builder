"""Typed Project AI policy, inheritance, request policy, and provenance."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import (
    LLMBackend,
    RequestOverrideMode,
    RetrievalStrategy,
    Settings,
    SourcePolicyDeploymentCap,
)
from app.core.exceptions import BadRequestError
from app.platform.providers.capabilities import (
    CAPABILITY_VERSION,
    describe_llm_capability,
    validate_generation_parameters,
)


class SourcePolicyMode(StrEnum):
    OFF = "off"
    OBSERVE = "observe"
    ENFORCE = "enforce"


class ProjectLLMPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: LLMBackend | None = None
    model: str | None = Field(default=None, min_length=1, max_length=128)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=128_000)


class ProjectRetrievalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: RetrievalStrategy | None = None
    top_k: int | None = Field(default=None, ge=1, le=100)
    rerank_enabled: bool | None = None
    rerank_top_n: int | None = Field(default=None, ge=1, le=100)
    rerank_score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class ProjectChatPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_context_chunks: int | None = Field(default=None, ge=1, le=50)
    context_char_budget: int | None = Field(default=None, ge=500, le=200_000)
    max_history_messages: int | None = Field(default=None, ge=0, le=200)
    include_citations: bool | None = None
    citation_excerpt_max_chars: int | None = Field(default=None, ge=0, le=2000)
    minimum_query_token_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    minimum_claim_token_coverage: float | None = Field(default=None, ge=0.0, le=1.0)


class ProjectAIConfig(BaseModel):
    """Sparse immutable revision payload; omitted values inherit deployment defaults."""

    model_config = ConfigDict(extra="forbid")

    llm: ProjectLLMPolicy = Field(default_factory=ProjectLLMPolicy)
    retrieval: ProjectRetrievalPolicy = Field(default_factory=ProjectRetrievalPolicy)
    chat: ProjectChatPolicy = Field(default_factory=ProjectChatPolicy)
    domain_instructions: str | None = Field(default=None, max_length=20_000)
    prompt_profile: str | None = Field(default=None, max_length=64)
    prompt_version: str | None = Field(default=None, max_length=64)
    source_policy_mode: SourcePolicyMode | None = None


class EffectiveLLMPolicy(BaseModel):
    provider: LLMBackend
    model: str
    temperature: float | None
    max_tokens: int


class EffectiveRetrievalPolicy(BaseModel):
    strategy: RetrievalStrategy
    top_k: int
    rerank_enabled: bool
    rerank_top_n: int
    rerank_score_threshold: float | None
    evidence_score_threshold: float


class EffectiveChatPolicy(BaseModel):
    max_context_chunks: int
    context_char_budget: int
    max_history_messages: int
    include_citations: bool
    citation_excerpt_max_chars: int
    minimum_query_token_coverage: float
    minimum_claim_token_coverage: float


class EffectiveProjectAIConfig(BaseModel):
    llm: EffectiveLLMPolicy
    retrieval: EffectiveRetrievalPolicy
    chat: EffectiveChatPolicy
    domain_instructions: str
    prompt_profile: str
    prompt_version: str
    source_policy_mode: SourcePolicyMode


class ConfigProvenance(BaseModel):
    project_config_revision_id: uuid.UUID | None = None
    project_config_revision_number: int | None = None
    project_config_hash: str | None = None
    global_config_fingerprint: str
    provider_capability_version: str = CAPABILITY_VERSION
    prompt_versions: dict[str, str]
    configured_source_policy_mode: SourcePolicyMode = SourcePolicyMode.OFF
    effective_source_policy_mode: SourcePolicyMode = SourcePolicyMode.OFF
    source_policy_deployment_cap: SourcePolicyDeploymentCap = (
        SourcePolicyDeploymentCap.ENFORCE
    )


class EffectiveConfigResolution(BaseModel):
    configuration: EffectiveProjectAIConfig
    configuration_hash: str
    origins: dict[str, str]
    provenance: ConfigProvenance
    compatibility_diagnostics: list[str] = Field(default_factory=list)

    def secret_free_snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "configuration": self.configuration.model_dump(mode="json"),
            "configuration_hash": self.configuration_hash,
            "origins": dict(self.origins),
            "provenance": self.provenance.model_dump(mode="json"),
            "compatibility_diagnostics": list(self.compatibility_diagnostics),
        }


class ConfigRevisionRecord(BaseModel):
    id: uuid.UUID
    revision_number: int
    configuration_hash: str
    configuration: dict[str, Any]


def resolve_project_ai_config(
    settings: Settings,
    revision: ConfigRevisionRecord | None,
    *,
    deprecated_overrides: dict[str, Any] | None = None,
) -> EffectiveConfigResolution:
    """Apply global -> Project -> compatibility overrides -> safety/capability rules."""
    project = (
        ProjectAIConfig.model_validate(revision.configuration)
        if revision is not None
        else ProjectAIConfig()
    )
    origins: dict[str, str] = {}

    def inherited(path: str, project_value: Any, global_value: Any) -> Any:
        if project_value is None:
            origins[path] = "global"
            return global_value
        origins[path] = "project"
        return project_value

    config = EffectiveProjectAIConfig(
        llm=EffectiveLLMPolicy(
            provider=inherited("llm.provider", project.llm.provider, settings.llm.backend),
            model=inherited("llm.model", project.llm.model, settings.llm.model),
            temperature=inherited(
                "llm.temperature", project.llm.temperature, settings.llm.temperature
            ),
            max_tokens=inherited("llm.max_tokens", project.llm.max_tokens, settings.llm.max_tokens),
        ),
        retrieval=EffectiveRetrievalPolicy(
            strategy=inherited(
                "retrieval.strategy", project.retrieval.strategy, settings.retrieval.strategy
            ),
            top_k=inherited(
                "retrieval.top_k", project.retrieval.top_k, settings.retrieval.default_top_k
            ),
            rerank_enabled=inherited(
                "retrieval.rerank_enabled",
                project.retrieval.rerank_enabled,
                settings.retrieval.rerank_enabled,
            ),
            rerank_top_n=inherited(
                "retrieval.rerank_top_n",
                project.retrieval.rerank_top_n,
                settings.retrieval.rerank_top_n,
            ),
            rerank_score_threshold=inherited(
                "retrieval.rerank_score_threshold",
                project.retrieval.rerank_score_threshold,
                settings.retrieval.rerank_score_threshold,
            ),
            evidence_score_threshold=inherited(
                "retrieval.evidence_score_threshold",
                project.retrieval.evidence_score_threshold,
                settings.chat.minimum_evidence_score,
            ),
        ),
        chat=EffectiveChatPolicy(
            max_context_chunks=inherited(
                "chat.max_context_chunks",
                project.chat.max_context_chunks,
                settings.chat.max_context_chunks,
            ),
            context_char_budget=inherited(
                "chat.context_char_budget",
                project.chat.context_char_budget,
                settings.chat.context_char_budget,
            ),
            max_history_messages=inherited(
                "chat.max_history_messages",
                project.chat.max_history_messages,
                settings.chat.max_history_messages,
            ),
            include_citations=inherited(
                "chat.include_citations",
                project.chat.include_citations,
                settings.chat.include_citations,
            ),
            citation_excerpt_max_chars=inherited(
                "chat.citation_excerpt_max_chars",
                project.chat.citation_excerpt_max_chars,
                settings.chat.citation_excerpt_max_chars,
            ),
            minimum_query_token_coverage=inherited(
                "chat.minimum_query_token_coverage",
                project.chat.minimum_query_token_coverage,
                settings.chat.minimum_query_token_coverage,
            ),
            minimum_claim_token_coverage=inherited(
                "chat.minimum_claim_token_coverage",
                project.chat.minimum_claim_token_coverage,
                settings.chat.minimum_claim_token_coverage,
            ),
        ),
        domain_instructions=inherited("domain_instructions", project.domain_instructions, ""),
        prompt_profile=inherited("prompt_profile", project.prompt_profile, "default"),
        prompt_version=inherited(
            "prompt_version", project.prompt_version, settings.chat.system_prompt_version
        ),
        source_policy_mode=inherited(
            "source_policy_mode", project.source_policy_mode, SourcePolicyMode.OFF
        ),
    )
    configured_source_policy_mode = config.source_policy_mode
    effective_source_policy_mode = cap_source_policy_mode(
        configured_source_policy_mode,
        settings.ai_policy.source_policy_deployment_cap,
    )
    if effective_source_policy_mode is not configured_source_policy_mode:
        config = config.model_copy(update={"source_policy_mode": effective_source_policy_mode})
        origins["source_policy_mode"] = "deployment_safety_cap"

    diagnostics: list[str] = []
    overrides = deprecated_overrides or {}
    explicit = dict(overrides)
    if explicit:
        diagnostics = sorted(explicit)
        if settings.ai_policy.request_override_mode is RequestOverrideMode.STRICT:
            raise BadRequestError(
                message="The request contains Project-owned AI policy overrides.",
                code="request_policy_override_forbidden",
                context={"fields": diagnostics},
            )
        payload = config.model_dump(mode="python")
        mapping = {
            "provider": ("llm", "provider"),
            "model": ("llm", "model"),
            "temperature": ("llm", "temperature"),
            "max_tokens": ("llm", "max_tokens"),
            "system_prompt_version": (None, "prompt_version"),
            "prompt_version": (None, "prompt_version"),
            "rerank": ("retrieval", "rerank_enabled"),
        }
        for key, value in explicit.items():
            if value is None:
                continue
            target = mapping.get(key)
            if target is None:
                continue
            section, field = target
            if section is None:
                payload[field] = value
                origins[field] = "deprecated_request_compatibility"
            else:
                payload[section][field] = value
                origins[f"{section}.{field}"] = "deprecated_request_compatibility"
        config = EffectiveProjectAIConfig.model_validate(payload)

    if config.retrieval.strategy not in settings.ai_policy.enabled_retrieval_strategies:
        raise BadRequestError(
            message="The configured retrieval strategy is not enabled.",
            code="retrieval_strategy_not_enabled",
        )
    capability = describe_llm_capability(config.llm.provider.value, config.llm.model)
    if (
        not capability.temperature.supported
        and config.llm.temperature is not None
        and origins["llm.temperature"] == "global"
    ):
        config = config.model_copy(
            update={"llm": config.llm.model_copy(update={"temperature": None})}
        )
        origins["llm.temperature"] = "provider_safe_omission"
    validate_generation_parameters(
        capability,
        temperature=config.llm.temperature,
        max_tokens=config.llm.max_tokens,
    )
    config_hash = stable_hash(config.model_dump(mode="json"))
    return EffectiveConfigResolution(
        configuration=config,
        configuration_hash=config_hash,
        origins=origins,
        provenance=ConfigProvenance(
            project_config_revision_id=revision.id if revision is not None else None,
            project_config_revision_number=(
                revision.revision_number if revision is not None else None
            ),
            project_config_hash=revision.configuration_hash if revision is not None else None,
            global_config_fingerprint=global_config_fingerprint(settings),
            prompt_versions={
                "chat": config.prompt_version,
                "profile": config.prompt_profile,
            },
            configured_source_policy_mode=configured_source_policy_mode,
            effective_source_policy_mode=effective_source_policy_mode,
            source_policy_deployment_cap=settings.ai_policy.source_policy_deployment_cap,
        ),
        compatibility_diagnostics=diagnostics,
    )


def apply_effective_ai_config(
    settings: Settings,
    resolution: EffectiveConfigResolution,
) -> Settings:
    """Overlay an effective Project policy while preserving live credentials."""
    effective = resolution.configuration
    return settings.model_copy(
        update={
            "llm": settings.llm.model_copy(
                update={
                    "backend": effective.llm.provider,
                    "model": effective.llm.model,
                    "temperature": effective.llm.temperature,
                    "max_tokens": effective.llm.max_tokens,
                }
            ),
            "retrieval": settings.retrieval.model_copy(
                update={
                    "strategy": effective.retrieval.strategy,
                    "default_top_k": effective.retrieval.top_k,
                    "rerank_enabled": effective.retrieval.rerank_enabled,
                    "rerank_top_n": effective.retrieval.rerank_top_n,
                    "rerank_score_threshold": effective.retrieval.rerank_score_threshold,
                }
            ),
            "chat": settings.chat.model_copy(
                update={
                    "retrieval_top_k": effective.retrieval.top_k,
                    "max_context_chunks": effective.chat.max_context_chunks,
                    "context_char_budget": effective.chat.context_char_budget,
                    "max_history_messages": effective.chat.max_history_messages,
                    "system_prompt_version": effective.prompt_version,
                    "include_citations": effective.chat.include_citations,
                    "citation_excerpt_max_chars": effective.chat.citation_excerpt_max_chars,
                    "minimum_evidence_score": effective.retrieval.evidence_score_threshold,
                    "minimum_query_token_coverage": (effective.chat.minimum_query_token_coverage),
                    "minimum_claim_token_coverage": (effective.chat.minimum_claim_token_coverage),
                }
            ),
        }
    )


def global_config_fingerprint(settings: Settings) -> str:
    return stable_hash(
        {
            "llm": settings.llm.model_dump(
                mode="json", exclude={"openai_api_key", "gemini_api_key"}
            ),
            "retrieval": settings.retrieval.model_dump(mode="json"),
            "chat": settings.chat.model_dump(mode="json"),
            "ai_policy": settings.ai_policy.model_dump(mode="json"),
        }
    )


def stable_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def cap_source_policy_mode(
    configured: SourcePolicyMode,
    deployment_cap: SourcePolicyDeploymentCap,
) -> SourcePolicyMode:
    """Apply the emergency rollout cap without changing stored Project policy."""
    order = {
        SourcePolicyMode.OFF: 0,
        SourcePolicyMode.OBSERVE: 1,
        SourcePolicyMode.ENFORCE: 2,
    }
    cap_mode = SourcePolicyMode(deployment_cap.value)
    return configured if order[configured] <= order[cap_mode] else cap_mode


def _json_default(value: object) -> str:
    if isinstance(value, (uuid.UUID, datetime)):
        return str(value)
    raise TypeError(f"Unsupported value for canonical JSON: {type(value).__name__}")
