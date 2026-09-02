"""Typed Project AI policy, inheritance, request policy, and provenance."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import (
    ChatConfig,
    EvidenceGateMode,
    EvidenceScoreMode,
    GroundingMode,
    LLMBackend,
    ModifiesExpansionMode,
    RequestOverrideMode,
    RerankerBackend,
    RerankMode,
    ResponseMode,
    RetrievalStrategy,
    Settings,
    SourcePolicyDeploymentCap,
    SourcePolicyMode,
    WebSearchBackend,
)
from app.core.exceptions import BadRequestError
from app.core.generation_models import (
    GENERATION_MODEL_REGISTRY_VERSION,
    generation_model_id_for_legacy_pair,
    generation_model_policy,
    resolve_generation_model,
)
from app.platform.config.catalog import catalog_entry
from app.platform.config.profiles import (
    PROFILE_REGISTRY_VERSION,
    calibration_profile_for,
    deployment_profile,
    execution_profile,
    execution_values,
    index_profile_for,
    matching_execution_profile,
    profile_hash,
)
from app.platform.providers.capabilities import (
    CAPABILITY_VERSION,
    describe_llm_capability,
    validate_generation_parameters,
)

logger = structlog.get_logger(__name__)


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
    semantic_candidate_top_k: int | None = Field(default=None, ge=1, le=200)
    keyword_candidate_top_k: int | None = Field(default=None, ge=1, le=200)
    hnsw_ef_search: int | None = Field(default=None, ge=1, le=1000)
    rrf_k: int | None = Field(default=None, ge=1, le=500)
    semantic_weight: float | None = Field(default=None, ge=0.0, le=10.0)
    keyword_weight: float | None = Field(default=None, ge=0.0, le=10.0)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    rerank_enabled: bool | None = None
    rerank_mode: RerankMode | None = None
    rerank_top_n: int | None = Field(default=None, ge=1, le=100)
    rerank_score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    min_ocr_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    passage_scoring_enabled: bool | None = None
    passage_window_tokens: int | None = Field(default=None, ge=16, le=512)
    passage_overlap_tokens: int | None = Field(default=None, ge=0, le=256)
    passage_min_tokens: int | None = Field(default=None, ge=8, le=256)
    rerank_candidate_window: int | None = Field(default=None, ge=1, le=100)
    rerank_return_n: int | None = Field(default=None, ge=1, le=100)
    query_translation_enabled: bool | None = None
    modifies_expansion_enabled: bool | None = None
    modifies_expansion_mode: ModifiesExpansionMode | None = None
    max_related_sources: int | None = Field(default=None, ge=1, le=8)
    max_relationship_candidates: int | None = Field(default=None, ge=1, le=20)
    max_chunks_per_document: int | None = Field(default=None, ge=1, le=100)
    max_chunks_per_section: int | None = Field(default=None, ge=1, le=100)
    deduplicate_by_content_hash: bool | None = None


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
    cross_language_semantic_evidence_score_threshold: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    minimum_query_token_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    minimum_claim_token_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    minimum_reranker_evidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    high_confidence_reranker_evidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    grounding_mode: GroundingMode | None = None
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


class ProjectAIConfigV1(BaseModel):
    """Historical sparse payload retained byte-for-byte and readable indefinitely."""

    model_config = ConfigDict(extra="forbid")

    llm: ProjectLLMPolicy = Field(default_factory=ProjectLLMPolicy)
    retrieval: ProjectRetrievalPolicy = Field(default_factory=ProjectRetrievalPolicy)
    chat: ProjectChatPolicy = Field(default_factory=ProjectChatPolicy)
    web_search: ProjectWebSearchPolicy = Field(default_factory=ProjectWebSearchPolicy)
    domain_instructions: str | None = Field(default=None, max_length=20_000)
    prompt_profile: str | None = Field(default=None, max_length=64)
    prompt_version: str | None = Field(default=None, max_length=64)
    source_policy_mode: SourcePolicyMode | None = None


class TranslationPolicy(StrEnum):
    INHERIT = "inherit"
    ENABLED = "enabled"
    DISABLED = "disabled"


class CanonicalRerankMode(StrEnum):
    ALWAYS = "always"
    CROSS_LANGUAGE = "cross_language"


class ProjectBehaviorV2(BaseModel):
    """Normal Project-owned behavior; provider and safety controls are intentionally absent."""

    model_config = ConfigDict(extra="forbid")

    response_mode: ResponseMode | None = None
    grounding_assurance: GroundingMode | None = None
    domain_instructions: str | None = Field(default=None, max_length=20_000)
    translation_policy: TranslationPolicy = TranslationPolicy.INHERIT
    generation_model_id: str | None = Field(default=None, min_length=1, max_length=128)


class ProjectExecutionV2(BaseModel):
    """RAG profile selection plus explicit values used only by Custom."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Literal["inherit", "standard", "quality", "economy", "custom"] | None = None
    retrieval_top_k: int | None = Field(default=None, ge=1, le=100)
    semantic_candidate_top_k: int | None = Field(default=None, ge=1, le=200)
    keyword_candidate_top_k: int | None = Field(default=None, ge=1, le=200)
    hnsw_ef_search: int | None = Field(default=None, ge=1, le=1000)
    rrf_k: int | None = Field(default=None, ge=1, le=500)
    semantic_weight: float | None = Field(default=None, ge=0.0, le=10.0)
    keyword_weight: float | None = Field(default=None, ge=0.0, le=10.0)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    rerank_mode: CanonicalRerankMode | None = None
    rerank_candidate_window: int | None = Field(default=None, ge=1, le=100)
    rerank_return_count: int | None = Field(default=None, ge=1, le=100)
    rerank_score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    min_ocr_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    max_chunks_per_document: int | None = Field(default=None, ge=1, le=100)
    max_chunks_per_section: int | None = Field(default=None, ge=1, le=100)
    deduplicate_by_content_hash: bool | None = None
    passage_scoring_enabled: bool | None = None
    passage_window_tokens: int | None = Field(default=None, ge=16, le=512)
    passage_overlap_tokens: int | None = Field(default=None, ge=0, le=256)
    passage_min_tokens: int | None = Field(default=None, ge=8, le=256)
    max_related_sources: int | None = Field(default=None, ge=1, le=8)
    max_relationship_candidates: int | None = Field(default=None, ge=1, le=20)
    max_context_chunks: int | None = Field(default=None, ge=1, le=50)
    context_char_budget: int | None = Field(default=None, ge=500, le=200_000)
    max_history_messages: int | None = Field(default=None, ge=0, le=200)

    @model_validator(mode="before")
    @classmethod
    def infer_legacy_custom_selection(cls, value: object) -> object:
        if not isinstance(value, dict) or "profile_id" in value:
            return value
        if value:
            return {**value, "profile_id": "custom"}
        return value

    @model_validator(mode="after")
    def validate_execution_values(self) -> ProjectExecutionV2:
        if (
            self.rerank_candidate_window is not None
            and self.rerank_return_count is not None
            and self.rerank_return_count > self.rerank_candidate_window
        ):
            raise ValueError("rerank_return_count must not exceed rerank_candidate_window")
        if (
            self.passage_overlap_tokens is not None
            and self.passage_window_tokens is not None
            and self.passage_overlap_tokens >= self.passage_window_tokens
        ):
            raise ValueError("passage_overlap_tokens must be smaller than passage_window_tokens")
        return self


class ProjectAIConfig(BaseModel):
    """Canonical V2 Project contract used for every new write and ordinary restore."""

    model_config = ConfigDict(extra="forbid")

    behavior: ProjectBehaviorV2 = Field(default_factory=ProjectBehaviorV2)
    execution: ProjectExecutionV2 = Field(default_factory=ProjectExecutionV2)


class EffectiveLLMPolicy(BaseModel):
    generation_model_id: str | None = None
    provider: LLMBackend
    model: str
    temperature: float | None
    max_tokens: int


class EffectiveRetrievalPolicy(BaseModel):
    strategy: RetrievalStrategy
    top_k: int
    semantic_candidate_top_k: int = 50
    keyword_candidate_top_k: int = 50
    hnsw_ef_search: int = 100
    rrf_k: int = 60
    semantic_weight: float = 1.0
    keyword_weight: float = 1.0
    rerank_enabled: bool
    rerank_mode: RerankMode = RerankMode.ALWAYS
    rerank_top_n: int
    score_threshold: float | None = None
    rerank_score_threshold: float | None
    min_ocr_confidence: float | None = None
    semantic_evidence_score_threshold: float
    passage_scoring_enabled: bool = False
    passage_window_tokens: int = 96
    passage_overlap_tokens: int = 24
    passage_min_tokens: int = 32
    rerank_candidate_window: int = 25
    rerank_return_n: int = 8
    rerank_return_count: int = 8
    reranker_backend: RerankerBackend | None = None
    reranker_model: str | None = None
    query_translation_enabled: bool = False
    query_translation_backend: str | None = None
    query_translation_model: str | None = None
    query_translation_prompt_version: str | None = None
    modifies_expansion_enabled: bool = False
    modifies_expansion_mode: ModifiesExpansionMode = ModifiesExpansionMode.OFF
    max_related_sources: int = 8
    max_relationship_candidates: int = 20
    max_chunks_per_document: int = 4
    max_chunks_per_section: int = 2
    deduplicate_by_content_hash: bool = True


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
    cross_language_semantic_evidence_score_threshold: float = 0.30
    minimum_claim_token_coverage: float
    minimum_claim_semantic_score: float = 0.25
    minimum_reranker_evidence_score: float = 0.40
    high_confidence_reranker_evidence_score: float = 0.70
    grounding_mode: GroundingMode = GroundingMode.STRICT
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


class InvariantState(BaseModel):
    hybrid_retrieval: bool
    hosted_reranking_stage: bool
    evidence_gate_enforced: bool
    content_hash_deduplication: bool | None
    durable_citation_provenance: bool
    governed_source_policy: bool
    governed_modifies_expansion: bool
    candidate_wise_grounding_invariant: bool = False


class StructuredOrigin(BaseModel):
    path: str
    layer: str
    category: str
    owner: str
    lifecycle: str
    effect_timing: str


class ConfigProvenance(BaseModel):
    project_config_revision_id: uuid.UUID | None = None
    project_config_revision_number: int | None = None
    project_config_hash: str | None = None
    project_config_schema_version: int | None = None
    global_config_fingerprint: str
    resolution_schema_version: int = 1
    provider_capability_version: str = CAPABILITY_VERSION
    generation_model_registry_version: str | None = None
    profile_registry_version: str | None = None
    # Phase-1 snapshots predate profile identity. Keep these nullable so an old
    # pinned conversation remains readable without attributing today's profile
    # metadata to a historical resolution that never captured it.
    deployment_profile_id: str | None = None
    deployment_profile_hash: str | None = None
    calibration_profile_id: str | None = None
    calibration_profile_hash: str | None = None
    execution_profile_id: str | None = None
    execution_profile_hash: str | None = None
    deployment_default_execution_profile_id: str | None = None
    execution_overrides: dict[str, Any] = Field(default_factory=dict)
    index_profile_id: str | None = None
    index_profile_hash: str | None = None
    prompt_versions: dict[str, str]
    configured_source_policy_mode: SourcePolicyMode = SourcePolicyMode.OFF
    effective_source_policy_mode: SourcePolicyMode = SourcePolicyMode.OFF
    source_policy_deployment_cap: SourcePolicyDeploymentCap = SourcePolicyDeploymentCap.ENFORCE


class EffectiveConfigResolution(BaseModel):
    configuration: EffectiveProjectAIConfig
    configuration_hash: str
    effective_value_hash: str
    resolution_fingerprint: str
    origins: dict[str, str]
    structured_origins: dict[str, StructuredOrigin]
    provenance: ConfigProvenance
    invariants: InvariantState
    compatibility_diagnostics: list[str] = Field(default_factory=list)

    def secret_free_snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": 4,
            "configuration": self.configuration.model_dump(mode="json"),
            "configuration_hash": self.configuration_hash,
            "effective_value_hash": self.effective_value_hash,
            "resolution_fingerprint": self.resolution_fingerprint,
            "origins": dict(self.origins),
            "structured_origins": {
                path: origin.model_dump(mode="json")
                for path, origin in self.structured_origins.items()
            },
            "provenance": self.provenance.model_dump(mode="json"),
            "invariants": self.invariants.model_dump(mode="json"),
            "compatibility_diagnostics": list(self.compatibility_diagnostics),
        }


class ConfigRevisionRecord(BaseModel):
    id: uuid.UUID
    revision_number: int
    configuration_hash: str
    configuration: dict[str, Any]
    schema_version: int = Field(default=1, ge=1, le=2)


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


def _resolve_modifies_expansion_mode(
    project: ProjectRetrievalPolicy,
    settings: Settings,
    origins: dict[str, str],
) -> ModifiesExpansionMode:
    """Project mode wins; legacy enabled=true still means expand."""
    if project.modifies_expansion_mode is not None:
        origins["retrieval.modifies_expansion_mode"] = "project"
        return project.modifies_expansion_mode
    if project.modifies_expansion_enabled is True:
        origins["retrieval.modifies_expansion_mode"] = "project"
        return ModifiesExpansionMode.EXPAND
    if project.modifies_expansion_enabled is False:
        origins["retrieval.modifies_expansion_mode"] = "project"
        return ModifiesExpansionMode.OFF
    origins["retrieval.modifies_expansion_mode"] = "global"
    return settings.retrieval.resolved_modifies_expansion_mode()


def _v2_as_legacy_policy(
    settings: Settings,
    project: ProjectAIConfig,
    *,
    allow_candidate_profiles: bool,
) -> tuple[ProjectAIConfigV1, str | None, dict[str, Any]]:
    """Adapt the bounded V2 surface into the established effective-resolution machinery."""
    behavior = project.behavior
    stored_execution = project.execution
    overrides = stored_execution.model_dump(mode="python", exclude_none=True)
    overrides.pop("profile_id", None)
    project_profile_id = stored_execution.profile_id or "inherit"
    execution_profile_id = (
        settings.ai_policy.default_rag_profile
        if project_profile_id == "inherit"
        else project_profile_id
    )
    if execution_profile_id in {"standard", "quality", "economy"}:
        selected_profile = execution_profile(
            execution_profile_id,
            allow_candidate=allow_candidate_profiles,
        )
        # A preset is the exact registry bundle. Stored or ENV tuning cannot
        # silently mutate any profile-owned field.
        execution = ProjectExecutionV2.model_validate(
            {"profile_id": execution_profile_id, **execution_values(selected_profile)}
        )
    elif project_profile_id == "custom":
        # A Custom revision is a complete materialized bundle.  Do not allow a
        # persisted Project configuration to acquire values from a later global
        # profile (or raw ENV) just because a field was omitted.
        required = set(execution_values(execution_profile("standard", allow_candidate=True)))
        missing = sorted(required - set(overrides))
        if missing:
            raise BadRequestError(
                message="Custom RAG execution configurations must contain every execution field.",
                code="custom_execution_incomplete",
                context={"missing_fields": missing},
            )
        execution = ProjectExecutionV2.model_validate({"profile_id": "custom", **overrides})
    else:
        # Inherit means the deployment's raw Custom values, never stale values
        # that happen to coexist with an inherit marker.
        execution = ProjectExecutionV2(profile_id="custom")
    translation_enabled = {
        TranslationPolicy.INHERIT: None,
        TranslationPolicy.ENABLED: True,
        TranslationPolicy.DISABLED: False,
    }[behavior.translation_policy]
    return (
        ProjectAIConfigV1(
            retrieval=ProjectRetrievalPolicy(
                top_k=execution.retrieval_top_k,
                semantic_candidate_top_k=execution.semantic_candidate_top_k,
                keyword_candidate_top_k=execution.keyword_candidate_top_k,
                hnsw_ef_search=execution.hnsw_ef_search,
                rrf_k=execution.rrf_k,
                semantic_weight=execution.semantic_weight,
                keyword_weight=execution.keyword_weight,
                score_threshold=execution.score_threshold,
                rerank_mode=(
                    RerankMode(execution.rerank_mode.value) if execution.rerank_mode else None
                ),
                rerank_top_n=execution.rerank_candidate_window,
                rerank_candidate_window=execution.rerank_candidate_window,
                rerank_return_n=execution.rerank_return_count,
                rerank_score_threshold=execution.rerank_score_threshold,
                min_ocr_confidence=execution.min_ocr_confidence,
                max_related_sources=execution.max_related_sources,
                max_relationship_candidates=execution.max_relationship_candidates,
                passage_scoring_enabled=execution.passage_scoring_enabled,
                passage_window_tokens=execution.passage_window_tokens,
                passage_overlap_tokens=execution.passage_overlap_tokens,
                passage_min_tokens=execution.passage_min_tokens,
                query_translation_enabled=translation_enabled,
                max_chunks_per_document=execution.max_chunks_per_document,
                max_chunks_per_section=execution.max_chunks_per_section,
                deduplicate_by_content_hash=execution.deduplicate_by_content_hash,
            ),
            chat=ProjectChatPolicy(
                response_mode=behavior.response_mode,
                grounding_mode=behavior.grounding_assurance,
                max_context_chunks=execution.max_context_chunks,
                context_char_budget=execution.context_char_budget,
                max_history_messages=execution.max_history_messages,
            ),
            domain_instructions=behavior.domain_instructions,
        ),
        execution_profile_id,
        overrides if project_profile_id == "custom" else {},
    )


def resolve_project_ai_config(
    settings: Settings,
    revision: ConfigRevisionRecord | None,
    *,
    deprecated_overrides: dict[str, Any] | None = None,
    validate_chat_response_policy: bool = True,
    validate_web_provider: bool = True,
    allow_candidate_profiles: bool = False,
) -> EffectiveConfigResolution:
    """Apply global -> Project -> compatibility overrides -> safety/capability rules."""
    schema_version = revision.schema_version if revision is not None else 2
    canonical_v2 = schema_version == 2
    project_v2 = (
        ProjectAIConfig.model_validate(revision.configuration)
        if revision is not None and canonical_v2
        else ProjectAIConfig()
    )
    execution_profile_id: str | None = None
    execution_overrides: dict[str, Any] = {}
    if canonical_v2:
        project, execution_profile_id, execution_overrides = _v2_as_legacy_policy(
            settings,
            project_v2,
            allow_candidate_profiles=allow_candidate_profiles,
        )
    else:
        assert revision is not None
        project = ProjectAIConfigV1.model_validate(revision.configuration)
    origins: dict[str, str] = {}

    deployment_capability = deployment_profile(settings)
    calibration = (
        calibration_profile_for(settings)
        if canonical_v2 and settings.runtime.capability_profile_id is not None
        else None
    )
    target_index_profile = index_profile_for(settings)

    def inherited(path: str, project_value: Any, global_value: Any) -> Any:
        if project_value is None:
            origins[path] = "global"
            return global_value
        origins[path] = "project"
        return project_value

    semantic_threshold = _inherit_semantic_evidence_threshold(
        project.retrieval.evidence_score_threshold,
        calibration.semantic_threshold
        if calibration is not None
        else settings.chat.minimum_semantic_evidence_score,
        origins,
    )
    rescue_floor = inherited(
        "chat.lexical_corroboration_floor_score",
        project.chat.lexical_corroboration_floor_score,
        calibration.lexical_floor
        if calibration is not None
        else settings.chat.lexical_corroboration_floor_score,
    )
    cross_language_semantic_threshold = inherited(
        "chat.cross_language_semantic_evidence_score_threshold",
        project.chat.cross_language_semantic_evidence_score_threshold,
        calibration.cross_language_semantic_threshold
        if calibration is not None
        else settings.chat.cross_language_semantic_evidence_score_threshold,
    )
    rescue_coverage = _inherit_lexical_corroboration_coverage(
        project.chat.minimum_query_token_coverage,
        calibration.lexical_coverage
        if calibration is not None
        else settings.chat.lexical_corroboration_coverage,
        origins,
    )
    rerank_mode = _resolve_rerank_mode(project.retrieval, settings, origins)
    modifies_mode = _resolve_modifies_expansion_mode(project.retrieval, settings, origins)
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

    generation_model_id: str | None = None
    if canonical_v2:
        selected_model = resolve_generation_model(
            settings,
            project_v2.behavior.generation_model_id,
        )
        generation_model_id = selected_model.id
        llm_provider = selected_model.provider
        llm_model = selected_model.model
        model_origin = (
            "project"
            if project_v2.behavior.generation_model_id is not None
            else "deployment_allowlist_default"
        )
        origins["llm.generation_model_id"] = model_origin
        origins["llm.provider"] = "generation_model_registry"
        origins["llm.model"] = "generation_model_registry"
    else:
        llm_provider = inherited("llm.provider", project.llm.provider, settings.llm.backend)
        llm_model = inherited("llm.model", project.llm.model, settings.llm.model)

    config = EffectiveProjectAIConfig(
        llm=EffectiveLLMPolicy(
            generation_model_id=generation_model_id,
            provider=llm_provider,
            model=llm_model,
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
            semantic_candidate_top_k=inherited(
                "retrieval.semantic_candidate_top_k",
                project.retrieval.semantic_candidate_top_k,
                settings.retrieval.semantic_candidate_top_k,
            ),
            keyword_candidate_top_k=inherited(
                "retrieval.keyword_candidate_top_k",
                project.retrieval.keyword_candidate_top_k,
                settings.retrieval.keyword_candidate_top_k,
            ),
            hnsw_ef_search=inherited(
                "retrieval.hnsw_ef_search",
                project.retrieval.hnsw_ef_search,
                settings.retrieval.hnsw_ef_search,
            ),
            rrf_k=inherited("retrieval.rrf_k", project.retrieval.rrf_k, settings.retrieval.rrf_k),
            semantic_weight=inherited(
                "retrieval.semantic_weight",
                project.retrieval.semantic_weight,
                settings.retrieval.semantic_weight,
            ),
            keyword_weight=inherited(
                "retrieval.keyword_weight",
                project.retrieval.keyword_weight,
                settings.retrieval.keyword_weight,
            ),
            rerank_mode=rerank_mode,
            rerank_enabled=rerank_mode is not RerankMode.OFF,
            rerank_top_n=inherited(
                "retrieval.rerank_top_n",
                project.retrieval.rerank_top_n,
                settings.retrieval.rerank_top_n,
            ),
            score_threshold=inherited(
                "retrieval.score_threshold",
                project.retrieval.score_threshold,
                settings.retrieval.score_threshold,
            ),
            rerank_score_threshold=inherited(
                "retrieval.rerank_score_threshold",
                project.retrieval.rerank_score_threshold,
                settings.retrieval.rerank_score_threshold,
            ),
            min_ocr_confidence=inherited(
                "retrieval.min_ocr_confidence",
                project.retrieval.min_ocr_confidence,
                settings.retrieval.min_ocr_confidence,
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
            rerank_return_count=inherited(
                "retrieval.rerank_return_count",
                project.retrieval.rerank_return_n,
                settings.retrieval.rerank_return_count,
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
            modifies_expansion_mode=modifies_mode,
            modifies_expansion_enabled=modifies_mode is ModifiesExpansionMode.EXPAND,
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
            max_chunks_per_document=inherited(
                "retrieval.max_chunks_per_document",
                project.retrieval.max_chunks_per_document,
                settings.retrieval.max_chunks_per_document,
            ),
            max_chunks_per_section=inherited(
                "retrieval.max_chunks_per_section",
                project.retrieval.max_chunks_per_section,
                settings.retrieval.max_chunks_per_section,
            ),
            deduplicate_by_content_hash=inherited(
                "retrieval.deduplicate_by_content_hash",
                project.retrieval.deduplicate_by_content_hash,
                settings.retrieval.deduplicate_by_content_hash,
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
                calibration.score_method
                if calibration is not None
                else settings.chat.evidence_score_mode,
            ),
            evidence_gate_mode=inherited(
                "chat.evidence_gate_mode",
                project.chat.evidence_gate_mode,
                settings.chat.evidence_gate_mode,
            ),
            lexical_corroboration_floor_score=rescue_floor,
            lexical_corroboration_coverage=rescue_coverage,
            cross_language_semantic_evidence_score_threshold=cross_language_semantic_threshold,
            minimum_claim_token_coverage=inherited(
                "chat.minimum_claim_token_coverage",
                project.chat.minimum_claim_token_coverage,
                calibration.minimum_claim_token_coverage
                if calibration is not None
                else settings.chat.minimum_claim_token_coverage,
            ),
            minimum_claim_semantic_score=(
                calibration.minimum_claim_semantic_score
                if calibration is not None
                else settings.chat.minimum_claim_semantic_score
            ),
            minimum_reranker_evidence_score=inherited(
                "chat.minimum_reranker_evidence_score",
                project.chat.minimum_reranker_evidence_score,
                calibration.minimum_reranker_score
                if calibration is not None
                else settings.chat.minimum_reranker_evidence_score,
            ),
            high_confidence_reranker_evidence_score=inherited(
                "chat.high_confidence_reranker_evidence_score",
                project.chat.high_confidence_reranker_evidence_score,
                calibration.high_confidence_reranker_score
                if calibration is not None
                else settings.chat.high_confidence_reranker_evidence_score,
            ),
            grounding_mode=inherited(
                "chat.grounding_mode",
                project.chat.grounding_mode,
                settings.chat.grounding_mode,
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
            "source_policy_mode",
            project.source_policy_mode,
            settings.ai_policy.source_policy_mode,
        ),
    )
    if execution_profile_id is not None:
        profile_origin_paths = {
            "retrieval_top_k": "retrieval.top_k",
            "semantic_candidate_top_k": "retrieval.semantic_candidate_top_k",
            "keyword_candidate_top_k": "retrieval.keyword_candidate_top_k",
            "hnsw_ef_search": "retrieval.hnsw_ef_search",
            "rrf_k": "retrieval.rrf_k",
            "semantic_weight": "retrieval.semantic_weight",
            "keyword_weight": "retrieval.keyword_weight",
            "score_threshold": "retrieval.score_threshold",
            "rerank_mode": "retrieval.rerank_mode",
            "rerank_candidate_window": "retrieval.rerank_candidate_window",
            "rerank_return_count": "retrieval.rerank_return_count",
            "rerank_score_threshold": "retrieval.rerank_score_threshold",
            "min_ocr_confidence": "retrieval.min_ocr_confidence",
            "max_chunks_per_document": "retrieval.max_chunks_per_document",
            "max_chunks_per_section": "retrieval.max_chunks_per_section",
            "deduplicate_by_content_hash": "retrieval.deduplicate_by_content_hash",
            "passage_scoring_enabled": "retrieval.passage_scoring_enabled",
            "passage_window_tokens": "retrieval.passage_window_tokens",
            "passage_overlap_tokens": "retrieval.passage_overlap_tokens",
            "passage_min_tokens": "retrieval.passage_min_tokens",
            "max_related_sources": "retrieval.max_related_sources",
            "max_relationship_candidates": "retrieval.max_relationship_candidates",
            "max_context_chunks": "chat.max_context_chunks",
            "context_char_budget": "chat.context_char_budget",
            "max_history_messages": "chat.max_history_messages",
        }
        profile_layer = (
            "custom_profile"
            if execution_profile_id == "custom"
            else (
                "global_execution_profile"
                if (project_v2.execution.profile_id or "inherit") == "inherit"
                else "project_execution_profile"
            )
        )
        for field, path in profile_origin_paths.items():
            if execution_profile_id == "custom" and field in execution_overrides:
                origins[path] = "project"
            elif execution_profile_id != "custom":
                origins[path] = profile_layer
    if calibration is not None:
        for path in (
            "retrieval.evidence_score_threshold",
            "chat.lexical_corroboration_floor_score",
            "chat.minimum_query_token_coverage",
            "chat.cross_language_semantic_evidence_score_threshold",
            "chat.evidence_score_mode",
            "chat.minimum_claim_token_coverage",
            "chat.minimum_claim_semantic_score",
            "chat.minimum_reranker_evidence_score",
            "chat.high_confidence_reranker_evidence_score",
        ):
            origins[path] = "calibration_profile"
    else:
        origins["chat.minimum_claim_semantic_score"] = "global"
    if canonical_v2:
        canonical_rerank_mode = (
            config.retrieval.rerank_mode
            if config.retrieval.rerank_mode is not RerankMode.OFF
            else RerankMode.ALWAYS
        )
        config = config.model_copy(
            update={
                "retrieval": config.retrieval.model_copy(
                    update={
                        "strategy": RetrievalStrategy.HYBRID,
                        "rerank_mode": canonical_rerank_mode,
                        "rerank_enabled": True,
                        "modifies_expansion_mode": ModifiesExpansionMode.EXPAND,
                        "modifies_expansion_enabled": True,
                    }
                ),
                "chat": config.chat.model_copy(
                    update={
                        "include_citations": True,
                        "evidence_gate_mode": EvidenceGateMode.ENFORCE,
                    }
                ),
                "source_policy_mode": SourcePolicyMode.ENFORCE,
            }
        )
        for path in (
            "retrieval.strategy",
            "retrieval.modifies_expansion_mode",
            "chat.include_citations",
            "chat.evidence_gate_mode",
            "source_policy_mode",
        ):
            origins[path] = "code_invariant"
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
        config.chat.cross_language_semantic_evidence_score_threshold
        > config.retrieval.semantic_evidence_score_threshold
    ):
        raise BadRequestError(
            message=(
                "cross_language_semantic_evidence_score_threshold must not exceed "
                "the semantic evidence score threshold."
            ),
            code="invalid_evidence_thresholds",
        )
    if (
        config.chat.high_confidence_reranker_evidence_score
        <= config.chat.minimum_reranker_evidence_score
    ):
        raise BadRequestError(
            message=(
                "high_confidence_reranker_evidence_score must be strictly greater than "
                "minimum_reranker_evidence_score."
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
    if not canonical_v2:
        diagnostics.append("v1_historical_revision")
    if not settings.cohere.api_key and settings.reranker.cohere_api_key:
        diagnostics.append("legacy_cohere_credential_fallback")
    overrides = deprecated_overrides or {}
    explicit = dict(overrides)
    if explicit:
        diagnostics.extend(sorted(explicit))
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
    provenance = ConfigProvenance(
        project_config_revision_id=revision.id if revision is not None else None,
        project_config_revision_number=(revision.revision_number if revision is not None else None),
        project_config_hash=revision.configuration_hash if revision is not None else None,
        project_config_schema_version=(revision.schema_version if revision is not None else None),
        global_config_fingerprint=global_config_fingerprint(settings),
        resolution_schema_version=4,
        generation_model_registry_version=GENERATION_MODEL_REGISTRY_VERSION,
        profile_registry_version=PROFILE_REGISTRY_VERSION,
        deployment_profile_id=deployment_capability.id,
        deployment_profile_hash=profile_hash(deployment_capability),
        calibration_profile_id=calibration.id if calibration is not None else None,
        calibration_profile_hash=profile_hash(calibration) if calibration is not None else None,
        execution_profile_id=execution_profile_id,
        execution_profile_hash=(
            profile_hash(execution_profile(execution_profile_id, allow_candidate=True))
            if execution_profile_id in {"standard", "quality", "economy"}
            else stable_hash(materialize_execution_values(config))
        ),
        deployment_default_execution_profile_id=settings.ai_policy.default_rag_profile,
        execution_overrides=execution_overrides,
        index_profile_id=target_index_profile.id,
        index_profile_hash=profile_hash(target_index_profile),
        prompt_versions={
            "chat": config.prompt_version,
            "profile": config.prompt_profile,
        },
        configured_source_policy_mode=configured_source_policy_mode,
        effective_source_policy_mode=effective_source_policy_mode,
        source_policy_deployment_cap=settings.ai_policy.source_policy_deployment_cap,
    )
    structured_origins = _build_structured_origins(origins, canonical_v2=canonical_v2)
    invariants = InvariantState(
        hybrid_retrieval=config.retrieval.strategy is RetrievalStrategy.HYBRID,
        hosted_reranking_stage=(
            config.retrieval.rerank_enabled
            and (
                not settings.app.is_production
                or "hosted" not in deployment_capability.feature_flags
                or settings.retrieval.reranker_backend is not RerankerBackend.NOOP
            )
        ),
        evidence_gate_enforced=config.chat.evidence_gate_mode is EvidenceGateMode.ENFORCE,
        content_hash_deduplication=config.retrieval.deduplicate_by_content_hash,
        durable_citation_provenance=config.chat.include_citations,
        governed_source_policy=config.source_policy_mode is SourcePolicyMode.ENFORCE,
        governed_modifies_expansion=(
            config.retrieval.modifies_expansion_mode is ModifiesExpansionMode.EXPAND
        ),
    )
    resolution_fingerprint = stable_hash(
        {
            "schema_version": 4,
            "effective_value_hash": config_hash,
            "origins": origins,
            "provenance": provenance.model_dump(mode="json"),
            "invariants": invariants.model_dump(mode="json"),
        }
    )
    return EffectiveConfigResolution(
        configuration=config,
        configuration_hash=config_hash,
        effective_value_hash=config_hash,
        resolution_fingerprint=resolution_fingerprint,
        origins=origins,
        structured_origins=structured_origins,
        provenance=provenance,
        invariants=invariants,
        compatibility_diagnostics=sorted(set(diagnostics)),
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
                    "semantic_candidate_top_k": effective.retrieval.semantic_candidate_top_k,
                    "keyword_candidate_top_k": effective.retrieval.keyword_candidate_top_k,
                    "hnsw_ef_search": effective.retrieval.hnsw_ef_search,
                    "rrf_k": effective.retrieval.rrf_k,
                    "semantic_weight": effective.retrieval.semantic_weight,
                    "keyword_weight": effective.retrieval.keyword_weight,
                    "rerank_mode": effective.retrieval.rerank_mode,
                    "score_threshold": effective.retrieval.score_threshold,
                    "rerank_score_threshold": effective.retrieval.rerank_score_threshold,
                    "min_ocr_confidence": effective.retrieval.min_ocr_confidence,
                    "passage_scoring_enabled": effective.retrieval.passage_scoring_enabled,
                    "passage_window_tokens": effective.retrieval.passage_window_tokens,
                    "passage_overlap_tokens": effective.retrieval.passage_overlap_tokens,
                    "passage_min_tokens": effective.retrieval.passage_min_tokens,
                    "rerank_candidate_window": effective.retrieval.rerank_candidate_window,
                    "rerank_return_count": effective.retrieval.rerank_return_count,
                    "modifies_expansion_mode": effective.retrieval.modifies_expansion_mode,
                    "max_related_sources": effective.retrieval.max_related_sources,
                    "max_relationship_candidates": (
                        effective.retrieval.max_relationship_candidates
                    ),
                    "max_chunks_per_document": effective.retrieval.max_chunks_per_document,
                    "max_chunks_per_section": effective.retrieval.max_chunks_per_section,
                    "deduplicate_by_content_hash": effective.retrieval.deduplicate_by_content_hash,
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
                    "cross_language_semantic_evidence_score_threshold": (
                        effective.chat.cross_language_semantic_evidence_score_threshold
                    ),
                    "minimum_claim_token_coverage": effective.chat.minimum_claim_token_coverage,
                    "minimum_claim_semantic_score": effective.chat.minimum_claim_semantic_score,
                    "minimum_reranker_evidence_score": (
                        effective.chat.minimum_reranker_evidence_score
                    ),
                    "high_confidence_reranker_evidence_score": (
                        effective.chat.high_confidence_reranker_evidence_score
                    ),
                    "grounding_mode": effective.chat.grounding_mode,
                    "candidate_wise_grounding_enabled": (
                        effective.chat.candidate_wise_grounding_enabled
                    ),
                }
            ),
        }
    )


class V1NormalizationResult(BaseModel):
    configuration: ProjectAIConfig
    compatibility_warnings: list[str]
    effective_diff: dict[str, dict[str, Any]]
    required_index_action: str = "none"


class V2ProfileNormalizationResult(BaseModel):
    configuration: ProjectAIConfig
    base_profile_id: str | None
    custom_execution: bool
    compatibility_warnings: list[str]
    effective_diff: dict[str, dict[str, Any]]
    required_index_action: str = "none"


def normalize_v1_project_config(
    settings: Settings,
    revision: ConfigRevisionRecord,
) -> V1NormalizationResult:
    """Canonicalize a historical V1 source into an append-only V2 write candidate."""
    if revision.schema_version != 1:
        raise BadRequestError(
            message="Only V1 Project configuration revisions require normalization.",
            code="project_config_normalization_not_required",
        )
    before = resolve_project_ai_config(settings, revision)
    effective = before.configuration
    warnings = [
        "V1 provider, web-budget, citation, source-policy, invariant, and raw-calibration "
        "controls are not carried into V2.",
    ]
    model_id = generation_model_id_for_legacy_pair(
        settings,
        provider=effective.llm.provider,
        model=effective.llm.model,
    )
    if model_id is None:
        model_id = generation_model_policy(settings)[1]
        warnings.append(
            "The V1 provider/model pair is not in the deployment allowlist; V2 uses the "
            "deployment default generation model ID."
        )
    candidate_window = max(
        effective.retrieval.rerank_top_n,
        effective.retrieval.rerank_candidate_window,
    )
    canonical = ProjectAIConfig(
        behavior=ProjectBehaviorV2(
            response_mode=effective.chat.response_mode,
            grounding_assurance=effective.chat.grounding_mode,
            domain_instructions=effective.domain_instructions or None,
            translation_policy=(
                TranslationPolicy.ENABLED
                if effective.retrieval.query_translation_enabled
                else TranslationPolicy.DISABLED
            ),
            generation_model_id=model_id,
        ),
        execution=ProjectExecutionV2(
            profile_id="custom",
            retrieval_top_k=effective.retrieval.top_k,
            semantic_candidate_top_k=effective.retrieval.semantic_candidate_top_k,
            keyword_candidate_top_k=effective.retrieval.keyword_candidate_top_k,
            hnsw_ef_search=effective.retrieval.hnsw_ef_search,
            rrf_k=effective.retrieval.rrf_k,
            semantic_weight=effective.retrieval.semantic_weight,
            keyword_weight=effective.retrieval.keyword_weight,
            score_threshold=effective.retrieval.score_threshold,
            rerank_mode=(
                CanonicalRerankMode.CROSS_LANGUAGE
                if effective.retrieval.rerank_mode is RerankMode.CROSS_LANGUAGE
                else CanonicalRerankMode.ALWAYS
            ),
            rerank_candidate_window=candidate_window,
            rerank_return_count=min(effective.retrieval.rerank_return_n, candidate_window),
            rerank_score_threshold=effective.retrieval.rerank_score_threshold,
            min_ocr_confidence=effective.retrieval.min_ocr_confidence,
            max_chunks_per_document=effective.retrieval.max_chunks_per_document,
            max_chunks_per_section=effective.retrieval.max_chunks_per_section,
            deduplicate_by_content_hash=effective.retrieval.deduplicate_by_content_hash,
            passage_scoring_enabled=effective.retrieval.passage_scoring_enabled,
            passage_window_tokens=effective.retrieval.passage_window_tokens,
            passage_overlap_tokens=effective.retrieval.passage_overlap_tokens,
            passage_min_tokens=effective.retrieval.passage_min_tokens,
            max_related_sources=effective.retrieval.max_related_sources,
            max_relationship_candidates=effective.retrieval.max_relationship_candidates,
            max_context_chunks=effective.chat.max_context_chunks,
            context_char_budget=effective.chat.context_char_budget,
            max_history_messages=effective.chat.max_history_messages,
        ),
    )
    execution_payload = canonical.execution.model_dump(mode="python", exclude_none=True)
    for field in ("score_threshold", "rerank_score_threshold", "min_ocr_confidence"):
        if field not in execution_payload:
            execution_payload[field] = 0.0
    canonical = canonical.model_copy(
        update={"execution": ProjectExecutionV2.model_validate(execution_payload)}
    )
    normalized_record = ConfigRevisionRecord(
        id=uuid.uuid4(),
        revision_number=revision.revision_number + 1,
        configuration_hash=stable_hash(canonical.model_dump(mode="json", exclude_none=True)),
        configuration=canonical.model_dump(mode="json", exclude_none=True),
        schema_version=2,
    )
    after = resolve_project_ai_config(settings, normalized_record)
    return V1NormalizationResult(
        configuration=canonical,
        compatibility_warnings=warnings,
        effective_diff=_effective_diff(
            before.configuration.model_dump(mode="json"),
            after.configuration.model_dump(mode="json"),
        ),
    )


def normalize_v2_project_config(
    settings: Settings,
    revision: ConfigRevisionRecord,
) -> V2ProfileNormalizationResult:
    """Materialize a profile-backed or explicit Custom V2 revision without changing behavior."""
    if revision.schema_version != 2:
        raise BadRequestError(
            message="Only V2 Project configuration revisions support profile normalization.",
            code="project_profile_normalization_requires_v2",
        )
    stored = ProjectAIConfig.model_validate(revision.configuration)
    before = resolve_project_ai_config(settings, revision)
    effective = before.configuration
    values = materialize_execution_values(effective)
    matched = matching_execution_profile(values)
    if matched is not None:
        profile_only = ProjectAIConfig(
            behavior=stored.behavior,
            execution=ProjectExecutionV2.model_validate({"profile_id": matched}),
        )
        profile_record = ConfigRevisionRecord(
            id=uuid.uuid4(),
            revision_number=revision.revision_number + 1,
            configuration_hash=stable_hash(profile_only.model_dump(mode="json", exclude_none=True)),
            configuration=profile_only.model_dump(mode="json", exclude_none=True),
            schema_version=2,
        )
        profile_values = materialize_execution_values(
            resolve_project_ai_config(settings, profile_record).configuration
        )
        if profile_values == values:
            execution = ProjectExecutionV2.model_validate({"profile_id": matched})
            warnings = []
        else:
            execution = ProjectExecutionV2.model_validate(
                {"profile_id": "custom", **values}
            )
            warnings = [
                "Advanced execution values differ from the preset and were materialized as "
                "Custom."
            ]
    else:
        execution = ProjectExecutionV2.model_validate({"profile_id": "custom", **values})
        warnings = [
            "No code-owned execution profile exactly matches this V2 revision; execution values "
            "remain explicit and are displayed as Custom."
        ]
    canonical = ProjectAIConfig(behavior=stored.behavior, execution=execution)
    normalized_record = ConfigRevisionRecord(
        id=uuid.uuid4(),
        revision_number=revision.revision_number + 1,
        configuration_hash=stable_hash(canonical.model_dump(mode="json", exclude_none=True)),
        configuration=canonical.model_dump(mode="json", exclude_none=True),
        schema_version=2,
    )
    after = resolve_project_ai_config(settings, normalized_record)
    return V2ProfileNormalizationResult(
        configuration=canonical,
        base_profile_id=matched,
        custom_execution=execution.profile_id == "custom",
        compatibility_warnings=warnings,
        effective_diff=_effective_diff(
            before.configuration.model_dump(mode="json"),
            after.configuration.model_dump(mode="json"),
        ),
    )


def materialize_execution_values(effective: EffectiveProjectAIConfig) -> dict[str, Any]:
    return {
        "retrieval_top_k": effective.retrieval.top_k,
        "semantic_candidate_top_k": effective.retrieval.semantic_candidate_top_k,
        "keyword_candidate_top_k": effective.retrieval.keyword_candidate_top_k,
        "hnsw_ef_search": effective.retrieval.hnsw_ef_search,
        "rrf_k": effective.retrieval.rrf_k,
        "semantic_weight": effective.retrieval.semantic_weight,
        "keyword_weight": effective.retrieval.keyword_weight,
        "score_threshold": effective.retrieval.score_threshold,
        "rerank_mode": effective.retrieval.rerank_mode.value,
        "rerank_candidate_window": effective.retrieval.rerank_candidate_window,
        "rerank_return_count": effective.retrieval.rerank_return_count,
        "rerank_score_threshold": effective.retrieval.rerank_score_threshold,
        "min_ocr_confidence": effective.retrieval.min_ocr_confidence,
        "max_chunks_per_document": effective.retrieval.max_chunks_per_document,
        "max_chunks_per_section": effective.retrieval.max_chunks_per_section,
        "deduplicate_by_content_hash": effective.retrieval.deduplicate_by_content_hash,
        "passage_scoring_enabled": effective.retrieval.passage_scoring_enabled,
        "passage_window_tokens": effective.retrieval.passage_window_tokens,
        "passage_overlap_tokens": effective.retrieval.passage_overlap_tokens,
        "passage_min_tokens": effective.retrieval.passage_min_tokens,
        "max_related_sources": effective.retrieval.max_related_sources,
        "max_relationship_candidates": effective.retrieval.max_relationship_candidates,
        "max_context_chunks": effective.chat.max_context_chunks,
        "context_char_budget": effective.chat.context_char_budget,
        "max_history_messages": effective.chat.max_history_messages,
    }


def _effective_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    left = _flatten(before)
    right = _flatten(after)
    return {
        path: {"before": left.get(path), "after": right.get(path)}
        for path in sorted(left.keys() | right.keys())
        if left.get(path) != right.get(path)
    }


def _flatten(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            flattened.update(_flatten(item, path))
        else:
            flattened[path] = item
    return flattened


def _build_structured_origins(
    origins: dict[str, str],
    *,
    canonical_v2: bool,
) -> dict[str, StructuredOrigin]:
    structured: dict[str, StructuredOrigin] = {}
    v2_paths = {
        "chat.response_mode": "project.v2.behavior.response_mode",
        "chat.grounding_mode": "project.v2.behavior.grounding_assurance",
        "domain_instructions": "project.v2.behavior.domain_instructions",
        "retrieval.query_translation_enabled": "project.v2.behavior.translation_policy",
        "llm.generation_model_id": "project.v2.behavior.generation_model_id",
        "retrieval.top_k": "project.v2.execution.retrieval_top_k",
        "retrieval.semantic_candidate_top_k": ("project.v2.execution.semantic_candidate_top_k"),
        "retrieval.keyword_candidate_top_k": "project.v2.execution.keyword_candidate_top_k",
        "retrieval.hnsw_ef_search": "project.v2.execution.hnsw_ef_search",
        "retrieval.rrf_k": "project.v2.execution.rrf_k",
        "retrieval.semantic_weight": "project.v2.execution.semantic_weight",
        "retrieval.keyword_weight": "project.v2.execution.keyword_weight",
        "retrieval.score_threshold": "project.v2.execution.score_threshold",
        "retrieval.rerank_mode": "project.v2.execution.rerank_mode",
        "retrieval.rerank_top_n": "project.v2.execution.rerank_candidate_window",
        "retrieval.rerank_candidate_window": ("project.v2.execution.rerank_candidate_window"),
        "retrieval.rerank_return_n": "project.v2.execution.rerank_return_count",
        "retrieval.rerank_return_count": "project.v2.execution.rerank_return_count",
        "retrieval.rerank_score_threshold": "project.v2.execution.rerank_score_threshold",
        "retrieval.min_ocr_confidence": "project.v2.execution.min_ocr_confidence",
        "retrieval.max_chunks_per_document": ("project.v2.execution.max_chunks_per_document"),
        "retrieval.max_chunks_per_section": "project.v2.execution.max_chunks_per_section",
        "retrieval.deduplicate_by_content_hash": (
            "project.v2.execution.deduplicate_by_content_hash"
        ),
        "retrieval.passage_scoring_enabled": ("project.v2.execution.passage_scoring_enabled"),
        "retrieval.passage_window_tokens": "project.v2.execution.passage_window_tokens",
        "retrieval.passage_overlap_tokens": "project.v2.execution.passage_overlap_tokens",
        "retrieval.passage_min_tokens": "project.v2.execution.passage_min_tokens",
        "retrieval.max_related_sources": "project.v2.execution.max_related_sources",
        "retrieval.max_relationship_candidates": (
            "project.v2.execution.max_relationship_candidates"
        ),
        "chat.max_context_chunks": "project.v2.execution.max_context_chunks",
        "chat.context_char_budget": "project.v2.execution.context_char_budget",
        "chat.max_history_messages": "project.v2.execution.max_history_messages",
    }
    for path, layer in origins.items():
        catalog_path = v2_paths.get(path) if canonical_v2 else None
        if catalog_path is None:
            catalog_path = f"settings.{path}"
        try:
            metadata = catalog_entry(catalog_path)
        except KeyError:
            metadata = catalog_entry(f"settings.{path}")
        structured[path] = StructuredOrigin(
            path=path,
            layer=layer,
            category=metadata.category.value,
            owner=metadata.owner.value,
            lifecycle=metadata.lifecycle,
            effect_timing=metadata.effect_timing,
        )
    return structured


def global_config_fingerprint(settings: Settings) -> str:
    capability = deployment_profile(settings)
    return stable_hash(
        {
            "deployment_profile": {"id": capability.id, "hash": profile_hash(capability)},
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
