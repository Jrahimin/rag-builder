"""Phase-1 configuration ownership, compatibility, and artifact regression gates."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.exceptions import BadRequestError
from app.modules.retrieval.schemas.search import SearchRequest
from app.platform.config.catalog import build_catalog, model_leaf_paths
from app.platform.config.index_artifact import (
    RequiredIndexAction,
    build_index_artifact_config,
    required_index_action,
)
from app.platform.config.project_ai import (
    ConfigProvenance,
    ConfigRevisionRecord,
    EffectiveProjectAIConfig,
    ProjectAIConfig,
    ProjectAIConfigV1,
    normalize_v1_project_config,
    resolve_project_ai_config,
    stable_hash,
)
from app.platform.jobs.contracts import JobConfiguration

pytestmark = pytest.mark.unit


def _revision(payload: dict[str, object], *, schema_version: int) -> ConfigRevisionRecord:
    return ConfigRevisionRecord(
        id=uuid.uuid4(),
        revision_number=7,
        configuration_hash=stable_hash(payload),
        configuration=payload,
        schema_version=schema_version,
    )


def test_catalog_covers_settings_v1_v2_effective_request_and_snapshot_surfaces() -> None:
    catalog = build_catalog(
        (Settings, "settings"),
        (ProjectAIConfigV1, "project.v1"),
        (ProjectAIConfig, "project.v2"),
        (EffectiveProjectAIConfig, "snapshot.effective"),
        (JobConfiguration, "snapshot.job"),
        (SearchRequest, "request.search"),
    )
    expected = set().union(
        model_leaf_paths(Settings, "settings"),
        model_leaf_paths(ProjectAIConfigV1, "project.v1"),
        model_leaf_paths(ProjectAIConfig, "project.v2"),
        model_leaf_paths(EffectiveProjectAIConfig, "snapshot.effective"),
        model_leaf_paths(JobConfiguration, "snapshot.job"),
        model_leaf_paths(SearchRequest, "request.search"),
    )

    assert expected <= catalog.keys()
    assert all(
        entry.owner and entry.lifecycle and entry.effect_timing for entry in catalog.values()
    )


def test_v2_surface_rejects_provider_calibration_web_and_invariant_controls() -> None:
    for payload in (
        {"llm": {"provider": "openai", "model": "arbitrary"}},
        {"retrieval": {"strategy": "semantic", "rerank_enabled": False}},
        {"chat": {"include_citations": False, "evidence_gate_mode": "observe"}},
        {"web_search": {"max_results": 20}},
        {"source_policy_mode": "off"},
    ):
        with pytest.raises(ValidationError):
            ProjectAIConfig.model_validate(payload)


def test_v2_enforces_invariants_but_candidate_wise_grounding_remains_internal() -> None:
    settings = Settings(
        retrieval={
            "strategy": "semantic",
            "rerank_mode": "off",
            "modifies_expansion_mode": "off",
        },
        chat={
            "include_citations": False,
            "evidence_gate_mode": "observe",
            "candidate_wise_grounding_enabled": True,
        },
        ai_policy={"source_policy_mode": "off"},
    )

    resolution = resolve_project_ai_config(settings, None)

    assert resolution.configuration.retrieval.strategy == "hybrid"
    assert resolution.configuration.retrieval.rerank_enabled is True
    assert resolution.configuration.retrieval.modifies_expansion_mode == "expand"
    assert resolution.configuration.chat.include_citations is True
    assert resolution.configuration.chat.evidence_gate_mode == "enforce"
    assert resolution.configuration.source_policy_mode == "enforce"
    assert resolution.configuration.chat.candidate_wise_grounding_enabled is True
    assert resolution.invariants.candidate_wise_grounding_invariant is False


def test_generation_model_selection_is_allowlisted_and_never_accepts_raw_model_strings() -> None:
    settings = Settings(
        ai_policy={
            "default_generation_model_id": "deployment-default",
            "allowed_generation_model_ids": [
                "deployment-default",
                "openai-gpt-4o-mini",
            ],
        }
    )
    allowed = _revision(
        {"behavior": {"generation_model_id": "openai-gpt-4o-mini"}},
        schema_version=2,
    )
    resolved = resolve_project_ai_config(settings, allowed)

    assert resolved.configuration.llm.generation_model_id == "openai-gpt-4o-mini"
    assert resolved.configuration.llm.provider == "openai"
    assert resolved.configuration.llm.model == "gpt-4o-mini"
    with pytest.raises(BadRequestError) as caught:
        resolve_project_ai_config(
            settings,
            _revision(
                {"behavior": {"generation_model_id": "openai/arbitrary-model"}},
                schema_version=2,
            ),
        )
    assert caught.value.code == "generation_model_not_allowed"


def test_v1_aliases_normalize_to_v2_without_copying_unsafe_or_dead_fields() -> None:
    payload = {
        "llm": {"provider": "echo", "model": "legacy-model"},
        "retrieval": {
            "strategy": "semantic",
            "top_k": 11,
            "rerank_enabled": False,
            "rerank_top_n": 40,
            "rerank_candidate_window": 25,
            "rerank_return_n": 7,
            "evidence_score_threshold": 0.55,
            "modifies_expansion_enabled": False,
        },
        "chat": {
            "include_citations": False,
            "evidence_gate_mode": "observe",
            "minimum_reranker_evidence_score": 0.65,
        },
        "web_search": {"max_results": 20},
        "source_policy_mode": "off",
    }
    result = normalize_v1_project_config(
        Settings(llm={"model": "legacy-model"}),
        _revision(payload, schema_version=1),
    )
    normalized = result.configuration.model_dump(mode="json", exclude_none=True)

    assert normalized["execution"]["retrieval_top_k"] == 11
    assert normalized["execution"]["rerank_candidate_window"] == 40
    assert normalized["execution"]["rerank_return_count"] == 7
    assert normalized["execution"]["rerank_mode"] == "always"
    assert "llm" not in normalized
    assert "web_search" not in normalized
    assert "source_policy_mode" not in normalized
    assert "evidence_score_threshold" not in str(normalized)
    assert result.required_index_action == "none"
    assert result.compatibility_warnings


def test_v1_resolution_remains_unsafe_but_readable_with_diagnostics() -> None:
    revision = _revision(
        {
            "retrieval": {"strategy": "semantic", "rerank_enabled": False},
            "chat": {"include_citations": False, "evidence_gate_mode": "observe"},
            "source_policy_mode": "off",
        },
        schema_version=1,
    )
    resolution = resolve_project_ai_config(Settings(), revision)

    assert resolution.configuration.retrieval.strategy == "semantic"
    assert resolution.configuration.retrieval.rerank_enabled is False
    assert resolution.configuration.chat.include_citations is False
    assert resolution.configuration.chat.evidence_gate_mode == "observe"
    assert resolution.configuration.source_policy_mode == "off"
    assert "v1_historical_revision" in resolution.compatibility_diagnostics


def test_index_artifact_fingerprint_changes_only_for_materialized_behavior() -> None:
    baseline_settings = Settings()
    query_tuned_settings = Settings(
        retrieval={
            "default_top_k": 31,
            "semantic_candidate_top_k": 80,
            "rerank_candidate_window": 40,
        },
        query_translation={"enabled": True},
        chat={"context_char_budget": 50_000},
    )
    artifact_changed_settings = Settings(chunking={"target_tokens": 300})
    baseline = build_index_artifact_config(baseline_settings)
    query_tuned = build_index_artifact_config(query_tuned_settings)
    artifact_changed = build_index_artifact_config(artifact_changed_settings)

    assert baseline == query_tuned
    assert required_index_action(baseline, query_tuned) is RequiredIndexAction.NONE
    assert required_index_action(baseline, artifact_changed) is RequiredIndexAction.REPROCESS


def test_index_identity_ignores_profile_labels_ocr_transport_and_query_filters() -> None:
    baseline_settings = Settings()
    metadata_only_settings = Settings(
        runtime={"capability_profile_id": "development"},
        ocr={"google_timeout_seconds": 90, "google_max_attempts": 7},
        retrieval={"min_ocr_confidence": 0.75},
    )
    baseline = build_index_artifact_config(baseline_settings)
    metadata_only = build_index_artifact_config(metadata_only_settings)

    assert baseline.index_profile_id is None
    assert metadata_only.index_profile_id == "development-hash"
    assert baseline.fingerprint_payload() == metadata_only.fingerprint_payload()
    assert required_index_action(baseline, metadata_only) is RequiredIndexAction.NONE
    assert (
        JobConfiguration(
            processing={},
            index={},
            quality={},
            index_artifact=baseline,
        ).index_output_digest()
        == JobConfiguration(
            processing={},
            index={},
            quality={},
            index_artifact=metadata_only,
        ).index_output_digest()
    )


def test_retired_env_inputs_fail_with_migration_guidance() -> None:
    with pytest.raises(ValidationError, match="auto_build_after_process"):
        Settings(retrieval={"auto_embed": True})
    with pytest.raises(ValidationError, match="did not affect output"):
        Settings(chunking={"overlap_tokens": 50})
    with pytest.raises(ValidationError, match="code-owned"):
        Settings(llm={"provider_version": "environment-version"})


def test_effective_hash_is_separate_from_resolution_fingerprint() -> None:
    first = resolve_project_ai_config(Settings(), None)
    second = resolve_project_ai_config(
        Settings(),
        _revision({}, schema_version=2),
    )

    assert first.effective_value_hash == second.effective_value_hash
    assert first.resolution_fingerprint != second.resolution_fingerprint
    assert first.secret_free_snapshot()["schema_version"] == 4
    assert first.structured_origins


def test_phase1_provenance_without_profile_fields_remains_readable() -> None:
    provenance = ConfigProvenance.model_validate(
        {
            "global_config_fingerprint": "a" * 64,
            "prompt_versions": {"chat": "v5", "profile": "default"},
        }
    )

    assert provenance.deployment_profile_id is None
    assert provenance.execution_profile_id is None
    assert provenance.index_profile_id is None
    assert provenance.resolution_schema_version == 1
    assert provenance.profile_registry_version is None
