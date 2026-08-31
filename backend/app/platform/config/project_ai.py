"""Typed Project AI policy, inheritance, request policy, and provenance."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import (
    ChatConfig,
    EvidenceGateMode,
    EvidenceScoreMode,
    LLMBackend,
    RequestOverrideMode,
    RerankerBackend,
    RerankMode,
    ResponseMode,
    RetrievalStrategy,
    Settings,
    SourcePolicyDeploymentCap,
    WebSearchBackend,
)
from app.core.exceptions import BadRequestError
from app.platform.providers.capabilities import (
    CAPABILITY_VERSION,
    describe_llm_capability,
    validate_generation_parameters,
)

logger = structlog.get_logger(__name__)


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
    """Sparse retrieval overrides. ``None`` inherits the deployment default.

    ``rerank_mode`` is the operator-facing control (Always / Cross-language / Off).
    Legacy ``rerank_enabled`` still maps true→always and false→off when mode is omitted.
    """

    model_config = ConfigDict(extra="forbid")

    strategy: RetrievalStrategy | None = None
    top_k: int | None = Field(default=None, ge=1, le=100)
    rerank_enabled: bool | None = None
    rerank_mode: RerankMode | None = None
    rerank_top_n: int | None = Field(default=None, ge=1, le=100)
    rerank_score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    passage_scoring_enabled: bool | None = None
    passage_window_tokens: int | None = Field(default=None, ge=16, le=512)
    passage_overlap_tokens: int | None = Field(default=None, ge=0, le=256)
    passage_min_tokens: int | None = Field(default=None, ge=8, le=256)
    rerank_candidate_window: int | None = Field(default=None, ge=1, le=100)
    rerank_return_n: int | None = Field(default=None, ge=1, le=100)
    query_translation_enabled: bool | None = None
    modifies_expansion_enabled: bool | None = None
    max_related_sources: int | None = Field(default=None, ge=1, le=8)
    max_relationship_candidates: int | None = Field(default=None, ge=1, le=20)


class ProjectChatPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_mode: ResponseMode | None = None
    max_context_chunks: int | None = Field(default=None, ge=1, le=50)
    context_char_budget: int | None = Field(default=None, ge=500, le=200_000)
    max_history_messages: int | None = Field(default=None, ge=0, le=200)
    include_citations: bool | None = None
    citation_excerpt_max_chars: int | None = Field(default=None, ge=0, le=2000)
    evidence_score_mode: EvidenceScoreMode | None = None
    evidence_gate_mode: EvidenceGateMode | None = None
    lexical_corroboration_floor_score: float | None = Field(default=None, ge=0.0, le=1.0)
    minimum_query_token_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    minimum_claim_token_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    minimum_reranker_evidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    candidate_wise_grounding_enabled: bool | None = None


class ProjectWebSearchPolicy(BaseModel):
    """Sparse per-Project controls for an operator-configured web provider."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    model: str | None = Field(default=None, min_length=1, max_length=128)
    max_results: int | None = Field(default=None, ge=1, le=20)
    max_evidence_chars: int | None = Field(default=None, ge=500, le=100_000)
    max_output_tokens: int | None = Field(default=None, ge=256, le=32_000)
    request_timeout_seconds: float | None = Field(default=None, ge=1.0, le=300.0)


class ProjectAIConfig(BaseModel):
    """Sparse immutable revision payload; omitted values inherit deployment defaults."""

    model_config = ConfigDict(extra="forbid")

    llm: ProjectLLMPolicy = Field(default_factory=ProjectLLMPolicy)
    retrieval: ProjectRetrievalPolicy = Field(default_factory=ProjectRetrievalPolicy)
    chat: ProjectChatPolicy = Field(default_factory=ProjectChatPolicy)
    web_search: ProjectWebSearchPolicy = Field(default_factory=ProjectWebSearchPolicy)
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
    rerank_mode: RerankMode = RerankMode.ALWAYS
    rerank_top_n: int
    rerank_score_threshold: float | None
    semantic_evidence_score_threshold: float
    passage_scoring_enabled: bool = False
    passage_window_tokens: int = 96
    passage_overlap_tokens: int = 24
    passage_min_tokens: int = 32
    rerank_candidate_window: int = 25
    rerank_return_n: int = 8
    reranker_backend: RerankerBackend | None = None
    reranker_model: str | None = None
    query_translation_enabled: bool = False
    query_translation_backend: str | None = None
    query_translation_model: str | None = None
    query_translation_prompt_version: str | None = None
    modifies_expansion_enabled: bool = False
    max_related_sources: int = 8
    max_relationship_candidates: int = 20


class EffectiveChatPolicy(BaseModel):
    response_mode: ResponseMode = ResponseMode.INDEXED_ONLY
    max_context_chunks: int
    context_char_budget: int
    max_history_messages: int
    include_citations: bool
    citation_excerpt_max_chars: int
    evidence_score_mode: EvidenceScoreMode = EvidenceScoreMode.WHOLE_CHUNK
    evidence_gate_mode: EvidenceGateMode = EvidenceGateMode.ENFORCE
    lexical_corroboration_floor_score: float
    lexical_corroboration_coverage: float
    minimum_claim_token_coverage: float
    minimum_reranker_evidence_score: float = 0.40
    candidate_wise_grounding_enabled: bool = False


class EffectiveWebSearchPolicy(BaseModel):
    enabled: bool
    backend: WebSearchBackend
    model: str
    max_results: int
    max_evidence_chars: int
    max_output_tokens: int
    request_timeout_seconds: float


class EffectiveProjectAIConfig(BaseModel):
    llm: EffectiveLLMPolicy
    retrieval: EffectiveRetrievalPolicy
    chat: EffectiveChatPolicy
    web_search: EffectiveWebSearchPolicy
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
    source_policy_deployment_cap: SourcePolicyDeploymentCap = SourcePolicyDeploymentCap.ENFORCE


class EffectiveConfigResolution(BaseModel):
    configuration: EffectiveProjectAIConfig
    configuration_hash: str
    origins: dict[str, str]
    provenance: ConfigProvenance
    compatibility_diagnostics: list[str] = Field(default_factory=list)

    def secret_free_snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
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


def _resolve_rerank_mode(
    project: ProjectRetrievalPolicy,
    settings: Settings,
    origins: dict[str, str],
) -> RerankMode:
    """Project rerank_mode wins; legacy rerank_enabled maps true→always, false→off."""
    if project.rerank_mode is not None:
        origins["retrieval.rerank_mode"] = "project"
        return project.rerank_mode
    if project.rerank_enabled is not None:
        origins["retrieval.rerank_mode"] = "project"
        return RerankMode.ALWAYS if project.rerank_enabled else RerankMode.OFF
    origins["retrieval.rerank_mode"] = "global"
    if not settings.retrieval.rerank_enabled:
        return RerankMode.OFF
    return settings.retrieval.rerank_mode


def resolve_project_ai_config(
    settings: Settings,
    revision: ConfigRevisionRecord | None,
    *,
    deprecated_overrides: dict[str, Any] | None = None,
    validate_chat_response_policy: bool = True,
    validate_web_provider: bool = True,
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

    semantic_threshold = _inherit_semantic_evidence_threshold(
        project.retrieval.evidence_score_threshold,
        settings.chat.minimum_semantic_evidence_score,
        origins,
    )
    rescue_floor = inherited(
        "chat.lexical_corroboration_floor_score",
        project.chat.lexical_corroboration_floor_score,
        settings.chat.lexical_corroboration_floor_score,
    )
    rescue_coverage = _inherit_lexical_corroboration_coverage(
        project.chat.minimum_query_token_coverage,
        settings.chat.lexical_corroboration_coverage,
        origins,
    )
    rerank_mode = _resolve_rerank_mode(project.retrieval, settings, origins)
    resolved_web_backend = settings.resolved_web_search_backend()
    origins["web_search.backend"] = (
        "global" if settings.web_search.backend is not None else "llm_backend"
    )
    default_web_enabled = resolved_web_backend is not WebSearchBackend.DISABLED
    if project.web_search.model is not None:
        web_model = project.web_search.model
        origins["web_search.model"] = "project"
    elif settings.web_search.model is not None:
        web_model = settings.web_search.model
        origins["web_search.model"] = "global"
    else:
        web_model = project.llm.model or settings.llm.model
        origins["web_search.model"] = (
            "project_llm" if project.llm.model is not None else "global_llm"
        )

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
            rerank_mode=rerank_mode,
            rerank_enabled=rerank_mode is not RerankMode.OFF,
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
            semantic_evidence_score_threshold=semantic_threshold,
            passage_scoring_enabled=inherited(
                "retrieval.passage_scoring_enabled",
                project.retrieval.passage_scoring_enabled,
                settings.retrieval.passage_scoring_enabled,
            ),
            passage_window_tokens=inherited(
                "retrieval.passage_window_tokens",
                project.retrieval.passage_window_tokens,
                settings.retrieval.passage_window_tokens,
            ),
            passage_overlap_tokens=inherited(
                "retrieval.passage_overlap_tokens",
                project.retrieval.passage_overlap_tokens,
                settings.retrieval.passage_overlap_tokens,
            ),
            passage_min_tokens=inherited(
                "retrieval.passage_min_tokens",
                project.retrieval.passage_min_tokens,
                settings.retrieval.passage_min_tokens,
            ),
            rerank_candidate_window=inherited(
                "retrieval.rerank_candidate_window",
                project.retrieval.rerank_candidate_window,
                settings.retrieval.rerank_candidate_window,
            ),
            rerank_return_n=inherited(
                "retrieval.rerank_return_n",
                project.retrieval.rerank_return_n,
                settings.retrieval.rerank_return_n,
            ),
            reranker_backend=settings.retrieval.reranker_backend,
            reranker_model=(
                settings.reranker.cohere_model
                if settings.retrieval.reranker_backend is RerankerBackend.COHERE
                else settings.retrieval.reranker_backend.value
            ),
            query_translation_enabled=inherited(
                "retrieval.query_translation_enabled",
                project.retrieval.query_translation_enabled,
                settings.query_translation.enabled,
            ),
            query_translation_backend=settings.query_translation.backend.value,
            query_translation_model=settings.query_translation.model,
            query_translation_prompt_version=settings.query_translation.prompt_version,
            modifies_expansion_enabled=inherited(
                "retrieval.modifies_expansion_enabled",
                project.retrieval.modifies_expansion_enabled,
                settings.retrieval.modifies_expansion_enabled,
            ),
            max_related_sources=inherited(
                "retrieval.max_related_sources",
                project.retrieval.max_related_sources,
                settings.retrieval.max_related_sources,
            ),
            max_relationship_candidates=inherited(
                "retrieval.max_relationship_candidates",
                project.retrieval.max_relationship_candidates,
                settings.retrieval.max_relationship_candidates,
            ),
        ),
        chat=EffectiveChatPolicy(
            response_mode=inherited(
                "chat.response_mode",
                project.chat.response_mode,
                settings.chat.response_mode,
            ),
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
            evidence_score_mode=inherited(
                "chat.evidence_score_mode",
                project.chat.evidence_score_mode,
                settings.chat.evidence_score_mode,
            ),
            evidence_gate_mode=inherited(
                "chat.evidence_gate_mode",
                project.chat.evidence_gate_mode,
                settings.chat.evidence_gate_mode,
            ),
            lexical_corroboration_floor_score=rescue_floor,
            lexical_corroboration_coverage=rescue_coverage,
            minimum_claim_token_coverage=inherited(
                "chat.minimum_claim_token_coverage",
                project.chat.minimum_claim_token_coverage,
                settings.chat.minimum_claim_token_coverage,
            ),
            minimum_reranker_evidence_score=inherited(
                "chat.minimum_reranker_evidence_score",
                project.chat.minimum_reranker_evidence_score,
                settings.chat.minimum_reranker_evidence_score,
            ),
            candidate_wise_grounding_enabled=inherited(
                "chat.candidate_wise_grounding_enabled",
                project.chat.candidate_wise_grounding_enabled,
                settings.chat.candidate_wise_grounding_enabled,
            ),
        ),
        web_search=EffectiveWebSearchPolicy(
            enabled=inherited(
                "web_search.enabled",
                project.web_search.enabled,
                default_web_enabled,
            ),
            backend=resolved_web_backend,
            model=web_model,
            max_results=inherited(
                "web_search.max_results",
                project.web_search.max_results,
                settings.web_search.max_results,
            ),
            max_evidence_chars=inherited(
                "web_search.max_evidence_chars",
                project.web_search.max_evidence_chars,
                settings.web_search.max_evidence_chars,
            ),
            max_output_tokens=inherited(
                "web_search.max_output_tokens",
                project.web_search.max_output_tokens,
                settings.web_search.max_output_tokens,
            ),
            request_timeout_seconds=inherited(
                "web_search.request_timeout_seconds",
                project.web_search.request_timeout_seconds,
                settings.web_search.request_timeout_seconds,
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
    if (
        config.chat.lexical_corroboration_floor_score
        > config.retrieval.semantic_evidence_score_threshold
    ):
        raise BadRequestError(
            message=(
                "lexical_corroboration_floor_score must not exceed "
                "the semantic evidence score threshold."
            ),
            code="invalid_evidence_thresholds",
        )
    if (
        config.chat.evidence_score_mode is EvidenceScoreMode.PASSAGE_MAX
        and not config.retrieval.passage_scoring_enabled
    ):
        raise BadRequestError(
            message="passage_max evidence mode requires passage scoring to be enabled.",
            code="passage_evidence_scoring_disabled",
        )
    if (
        config.chat.evidence_score_mode is EvidenceScoreMode.PASSAGE_MAX
        and config.retrieval.strategy is not RetrievalStrategy.HYBRID
    ):
        raise BadRequestError(
            message="passage_max evidence mode currently requires hybrid retrieval.",
            code="passage_evidence_requires_hybrid",
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

    if validate_chat_response_policy:
        _validate_web_response_policy(
            config,
            settings,
            require_provider=validate_web_provider,
        )
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
                    "rerank_mode": effective.retrieval.rerank_mode,
                    "rerank_top_n": effective.retrieval.rerank_top_n,
                    "rerank_score_threshold": effective.retrieval.rerank_score_threshold,
                    "passage_scoring_enabled": effective.retrieval.passage_scoring_enabled,
                    "passage_window_tokens": effective.retrieval.passage_window_tokens,
                    "passage_overlap_tokens": effective.retrieval.passage_overlap_tokens,
                    "passage_min_tokens": effective.retrieval.passage_min_tokens,
                    "rerank_candidate_window": effective.retrieval.rerank_candidate_window,
                    "rerank_return_n": effective.retrieval.rerank_return_n,
                    "modifies_expansion_enabled": (
                        effective.retrieval.modifies_expansion_enabled
                    ),
                    "max_related_sources": effective.retrieval.max_related_sources,
                    "max_relationship_candidates": (
                        effective.retrieval.max_relationship_candidates
                    ),
                    "reranker_backend": (
                        effective.retrieval.reranker_backend or settings.retrieval.reranker_backend
                    ),
                }
            ),
            "query_translation": settings.query_translation.model_copy(
                update={
                    "enabled": effective.retrieval.query_translation_enabled,
                    "backend": (
                        LLMBackend(effective.retrieval.query_translation_backend)
                        if effective.retrieval.query_translation_backend
                        else settings.query_translation.backend
                    ),
                    "model": (
                        effective.retrieval.query_translation_model
                        or settings.query_translation.model
                    ),
                    "prompt_version": (
                        effective.retrieval.query_translation_prompt_version
                        or settings.query_translation.prompt_version
                    ),
                }
            ),
            "web_search": settings.web_search.model_copy(
                update={
                    "backend": (
                        effective.web_search.backend
                        if effective.web_search.enabled
                        else WebSearchBackend.DISABLED
                    ),
                    "model": effective.web_search.model,
                    "max_results": effective.web_search.max_results,
                    "max_evidence_chars": effective.web_search.max_evidence_chars,
                    "max_output_tokens": effective.web_search.max_output_tokens,
                    "request_timeout_seconds": effective.web_search.request_timeout_seconds,
                }
            ),
            "chat": ChatConfig.model_validate(
                {
                    **settings.chat.model_dump(),
                    "retrieval_top_k": effective.retrieval.top_k,
                    "max_context_chunks": effective.chat.max_context_chunks,
                    "context_char_budget": effective.chat.context_char_budget,
                    "max_history_messages": effective.chat.max_history_messages,
                    "system_prompt_version": effective.prompt_version,
                    "response_mode": effective.chat.response_mode,
                    "include_citations": effective.chat.include_citations,
                    "citation_excerpt_max_chars": effective.chat.citation_excerpt_max_chars,
                    "evidence_score_mode": effective.chat.evidence_score_mode,
                    "evidence_gate_mode": effective.chat.evidence_gate_mode,
                    "minimum_semantic_evidence_score": (
                        effective.retrieval.semantic_evidence_score_threshold
                    ),
                    "lexical_corroboration_floor_score": (
                        effective.chat.lexical_corroboration_floor_score
                    ),
                    "lexical_corroboration_coverage": (
                        effective.chat.lexical_corroboration_coverage
                    ),
                    "minimum_claim_token_coverage": effective.chat.minimum_claim_token_coverage,
                    "minimum_reranker_evidence_score": (
                        effective.chat.minimum_reranker_evidence_score
                    ),
                    "candidate_wise_grounding_enabled": (
                        effective.chat.candidate_wise_grounding_enabled
                    ),
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
            "web_search": {
                **settings.web_search.model_dump(mode="json", exclude={"openai_api_key"}),
                "credential_configured": bool(settings.resolved_web_search_api_key()),
            },
            "ai_policy": settings.ai_policy.model_dump(mode="json"),
            "query_translation": settings.query_translation.model_dump(mode="json"),
            "reranker": settings.reranker.model_dump(
                mode="json",
                exclude={"cohere_api_key"},
            ),
            "cohere": {"configured": bool(settings.resolved_cohere_api_key())},
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


_LEGACY_RANKING_SCORE_CEILING = 0.15


def _inherit_semantic_evidence_threshold(
    project_value: float | None,
    global_value: float,
    origins: dict[str, str],
) -> float:
    """Ignore leftover RRF-scale project thresholds instead of failing chat."""
    if project_value is None:
        origins["retrieval.evidence_score_threshold"] = "global"
        return global_value
    if project_value < _LEGACY_RANKING_SCORE_CEILING:
        origins["retrieval.evidence_score_threshold"] = "global"
        logger.info(
            "ignored_legacy_evidence_score_threshold",
            project_value=project_value,
            applied_value=global_value,
        )
        return global_value
    origins["retrieval.evidence_score_threshold"] = "project"
    return project_value


def _inherit_lexical_corroboration_coverage(
    project_value: float | None,
    global_value: float,
    origins: dict[str, str],
) -> float:
    """Ignore leftover rejection-gate coverage values that would loosen rescue."""
    if project_value is None:
        origins["chat.minimum_query_token_coverage"] = "global"
        return global_value
    if project_value < global_value:
        origins["chat.minimum_query_token_coverage"] = "global"
        logger.info(
            "ignored_legacy_query_token_coverage",
            project_value=project_value,
            applied_value=global_value,
        )
        return global_value
    origins["chat.minimum_query_token_coverage"] = "project"
    return project_value


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


def _validate_web_response_policy(
    config: EffectiveProjectAIConfig,
    settings: Settings,
    *,
    require_provider: bool = True,
) -> None:
    if config.chat.response_mode is ResponseMode.INDEXED_ONLY:
        return
    if config.prompt_version != "v5":
        raise BadRequestError(
            message="Web-enabled response modes require the source-aware v5 chat prompt.",
            code="web_response_mode_requires_source_prompt",
        )
    if not require_provider:
        return
    if config.web_search.backend is WebSearchBackend.DISABLED:
        raise BadRequestError(
            message="Web-enabled response modes require a configured web-search provider.",
            code="web_search_not_configured",
        )
    if not config.web_search.enabled:
        raise BadRequestError(
            message="Web-enabled response modes are disabled for this Project.",
            code="web_search_disabled_for_project",
        )
    if not settings.resolved_web_search_api_key():
        raise BadRequestError(
            message="Web-enabled response modes require web-search credentials.",
            code="web_search_credentials_missing",
        )


def _json_default(value: object) -> str:
    if isinstance(value, (uuid.UUID, datetime)):
        return str(value)
    raise TypeError(f"Unsupported value for canonical JSON: {type(value).__name__}")
