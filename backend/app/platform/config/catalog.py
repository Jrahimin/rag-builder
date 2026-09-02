"""Authoritative ownership and lifecycle metadata for configuration surfaces."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class SettingCategory(StrEnum):
    INTERNAL_INVARIANT = "internal_invariant"
    DEPLOYMENT_INFRASTRUCTURE = "deployment_infrastructure"
    PROVIDER_CREDENTIALS = "provider_credentials"
    INGESTION_INDEX = "ingestion_index"
    CALIBRATION = "calibration"
    RAG_EXECUTION = "rag_execution"
    PROJECT_BEHAVIOR = "project_behavior"
    TEST_LAB_EXPERIMENTAL = "test_lab_experimental"
    DEPRECATED_COMPATIBILITY = "deprecated_compatibility"
    PROVENANCE_VERSION = "provenance_version"


class SettingOwner(StrEnum):
    CODE = "code"
    DEPLOYMENT = "deployment"
    PROJECT = "project"
    REQUEST = "request"
    SNAPSHOT = "snapshot"
    TEST_LAB = "test_lab"


class SettingCatalogEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    category: SettingCategory
    owner: SettingOwner
    lifecycle: str
    audience: str
    impact: str
    effect_timing: str
    compatibility_status: str = "canonical"
    replacement: str | None = None


_EXPLICIT: dict[str, SettingCatalogEntry] = {}


def _entry(
    path: str,
    category: SettingCategory,
    owner: SettingOwner,
    *,
    lifecycle: str,
    audience: str,
    impact: str,
    timing: str,
    compatibility: str = "canonical",
    replacement: str | None = None,
) -> None:
    _EXPLICIT[path] = SettingCatalogEntry(
        path=path,
        category=category,
        owner=owner,
        lifecycle=lifecycle,
        audience=audience,
        impact=impact,
        effect_timing=timing,
        compatibility_status=compatibility,
        replacement=replacement,
    )


for _path in (
    "project.v2.behavior.response_mode",
    "project.v2.behavior.grounding_assurance",
    "project.v2.behavior.domain_instructions",
    "project.v2.behavior.translation_policy",
    "project.v2.behavior.generation_model_id",
):
    _entry(
        _path,
        SettingCategory.PROJECT_BEHAVIOR,
        SettingOwner.PROJECT,
        lifecycle="immutable_revision",
        audience="operator",
        impact="query_time",
        timing="next_conversation_or_explicit_snapshot_update",
    )

_entry(
    "project.v2.execution.profile_id",
    SettingCategory.RAG_EXECUTION,
    SettingOwner.PROJECT,
    lifecycle="immutable_revision",
    audience="operator",
    impact="query_time",
    timing="next_conversation_or_explicit_snapshot_update",
)

_entry(
    "settings.ai_policy.default_rag_profile",
    SettingCategory.RAG_EXECUTION,
    SettingOwner.DEPLOYMENT,
    lifecycle="process_startup",
    audience="deployment_operator",
    impact="query_time",
    timing="new_conversations_jobs_and_standalone_retrieval",
)

for _path, _category, _impact in (
    ("settings.runtime.capability_profile_id", SettingCategory.PROVENANCE_VERSION, "startup"),
    ("profiles.deployment", SettingCategory.PROVENANCE_VERSION, "provider_capability"),
    ("profiles.calibration", SettingCategory.CALIBRATION, "query_quality"),
    ("profiles.execution", SettingCategory.RAG_EXECUTION, "query_time"),
    ("profiles.index", SettingCategory.INGESTION_INDEX, "artifact_identity"),
):
    _entry(
        _path,
        _category,
        SettingOwner.CODE,
        lifecycle="immutable_versioned_registry",
        audience="super_admin",
        impact=_impact,
        timing="new_resolution_or_build_only",
    )

for _path in (
    "project.v2.execution.retrieval_top_k",
    "project.v2.execution.semantic_candidate_top_k",
    "project.v2.execution.keyword_candidate_top_k",
    "project.v2.execution.hnsw_ef_search",
    "project.v2.execution.rrf_k",
    "project.v2.execution.semantic_weight",
    "project.v2.execution.keyword_weight",
    "project.v2.execution.rerank_mode",
    "project.v2.execution.rerank_candidate_window",
    "project.v2.execution.rerank_return_count",
    "project.v2.execution.max_chunks_per_document",
    "project.v2.execution.max_chunks_per_section",
    "project.v2.execution.passage_scoring_enabled",
    "project.v2.execution.passage_window_tokens",
    "project.v2.execution.passage_overlap_tokens",
    "project.v2.execution.passage_min_tokens",
    "project.v2.execution.max_context_chunks",
    "project.v2.execution.context_char_budget",
    "project.v2.execution.max_history_messages",
):
    _entry(
        _path,
        SettingCategory.RAG_EXECUTION,
        SettingOwner.PROJECT,
        lifecycle="immutable_revision",
        audience="advanced_operator",
        impact="query_time",
        timing="next_conversation_or_explicit_snapshot_update",
    )

for _path, _replacement in {
    "project.v1.llm.provider": "deployment provider + behavior.generation_model_id",
    "project.v1.llm.model": "behavior.generation_model_id",
    "project.v1.retrieval.rerank_enabled": "execution.rerank_mode",
    "project.v1.retrieval.rerank_top_n": "execution.rerank_candidate_window",
    "project.v1.retrieval.modifies_expansion_enabled": "code-owned conditional governance",
    "project.v1.chat.include_citations": "code-owned invariant",
    "project.v1.source_policy_mode": "code-owned conditional governance",
}.items():
    _entry(
        _path,
        SettingCategory.DEPRECATED_COMPATIBILITY,
        SettingOwner.SNAPSHOT,
        lifecycle="historical_read_only",
        audience="super_admin",
        impact="legacy_resolution",
        timing="pinned_or_active_v1_only",
        compatibility="v1_read_only",
        replacement=_replacement,
    )


def catalog_entry(path: str) -> SettingCatalogEntry:
    """Return explicit metadata or a deterministic deployment-owned classification."""
    explicit = _EXPLICIT.get(path)
    if explicit is not None:
        return explicit
    if path.startswith("settings."):
        section = path.split(".", 2)[1]
        if section in {"database", "redis", "minio", "storage", "jobs", "server", "cors"}:
            category = SettingCategory.DEPLOYMENT_INFRASTRUCTURE
            impact = "runtime"
        elif section in {"llm", "embedding", "ocr", "cohere", "reranker", "web_search"}:
            category = SettingCategory.PROVIDER_CREDENTIALS
            impact = "provider_capability"
        elif section in {"parsing", "chunking"}:
            category = SettingCategory.INGESTION_INDEX
            impact = "artifact_identity"
        elif section in {"chat", "evaluation"}:
            category = SettingCategory.CALIBRATION
            impact = "query_quality"
        elif section in {"retrieval", "query_translation"}:
            category = SettingCategory.RAG_EXECUTION
            impact = "query_or_artifact_as_catalogued"
        else:
            category = SettingCategory.DEPLOYMENT_INFRASTRUCTURE
            impact = "runtime"
        return SettingCatalogEntry(
            path=path,
            category=category,
            owner=SettingOwner.DEPLOYMENT,
            lifecycle="process_startup",
            audience="deployment_operator",
            impact=impact,
            effect_timing="restart",
        )
    if path.startswith("project.v1."):
        return SettingCatalogEntry(
            path=path,
            category=SettingCategory.DEPRECATED_COMPATIBILITY,
            owner=SettingOwner.SNAPSHOT,
            lifecycle="historical_read_only",
            audience="super_admin",
            impact="legacy_resolution",
            effect_timing="pinned_or_active_v1_only",
            compatibility_status="v1_read_only",
            replacement="normalize through the canonical V2 contract",
        )
    if path.startswith("request."):
        return SettingCatalogEntry(
            path=path,
            category=SettingCategory.RAG_EXECUTION,
            owner=SettingOwner.REQUEST,
            lifecycle="single_request",
            audience="api_client",
            impact="query_time",
            effect_timing="immediate",
        )
    if path.startswith("snapshot."):
        return SettingCatalogEntry(
            path=path,
            category=SettingCategory.PROVENANCE_VERSION,
            owner=SettingOwner.SNAPSHOT,
            lifecycle="immutable",
            audience="operator",
            impact="reproducibility",
            effect_timing="captured_at_creation",
        )
    raise KeyError(path)


def model_leaf_paths(model: type[BaseModel], prefix: str) -> set[str]:
    """Enumerate Pydantic leaf paths for catalog completeness tests and docs."""
    paths: set[str] = set()

    def visit(current: type[BaseModel], current_prefix: str) -> None:
        for name, field in current.model_fields.items():
            annotation = field.annotation
            nested = (
                annotation
                if isinstance(annotation, type) and issubclass(annotation, BaseModel)
                else None
            )
            if nested is None:
                paths.add(f"{current_prefix}.{name}")
            else:
                visit(nested, f"{current_prefix}.{name}")

    visit(model, prefix)
    return paths


def build_catalog(*model_surfaces: tuple[type[BaseModel], str]) -> dict[str, SettingCatalogEntry]:
    """Build and validate a complete catalog for supplied typed surfaces."""
    paths = set(_EXPLICIT)
    for model, prefix in model_surfaces:
        paths.update(model_leaf_paths(model, prefix))
    return {path: catalog_entry(path) for path in sorted(paths)}
