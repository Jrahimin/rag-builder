"""Phase 2 immutable profile, resolver, index identity, and certification tests."""

from __future__ import annotations

import uuid

import pytest

import app.platform.config.profiles as profile_registry
from app.core.config import Settings
from app.modules.evaluation.profile_certification import (
    HOSTED_RAG_CERTIFICATION_MANIFEST,
    EvaluationSuiteRequirement,
    EvaluationSuiteResult,
    ProfileCertificationInput,
    ProfileCertificationManifest,
    ProfileMetrics,
    evaluate_profile_candidate,
)
from app.platform.config.index_artifact import build_index_artifact_config
from app.platform.config.profiles import (
    PROFILE_CERTIFICATIONS,
    RAG_EXECUTION_PROFILES,
    CertificationStatus,
    execution_profile,
    execution_values,
    profile_hash,
    registry_errors,
    validate_profile_compatibility,
)
from app.platform.config.project_ai import (
    ConfigRevisionRecord,
    materialize_execution_values,
    normalize_v2_project_config,
    resolve_project_ai_config,
)


def _revision(configuration: dict[str, object]) -> ConfigRevisionRecord:
    return ConfigRevisionRecord(
        id=uuid.uuid4(),
        revision_number=1,
        configuration_hash="a" * 64,
        configuration=configuration,
        schema_version=2,
    )


def _metrics(**updates: float) -> ProfileMetrics:
    values = {
        "recall_at_k": 0.90,
        "ndcg": 0.85,
        "false_accept_rate": 0.0,
        "false_refusal_rate": 0.05,
        "groundedness": 1.0,
        "citation_coverage": 1.0,
        "p95_latency_ms": 100.0,
        "provider_cost": 1.0,
    }
    values.update(updates)
    return ProfileMetrics.model_validate(values)


def _hosted_suite_results(*, tax_passed: int = 21) -> tuple[EvaluationSuiteResult, ...]:
    return (
        EvaluationSuiteResult(
            suite_id="tax", suite_version="v1", cases_passed=tax_passed, cases_total=21
        ),
        EvaluationSuiteResult(
            suite_id="ci-smoke", suite_version="v1", cases_passed=12, cases_total=12
        ),
        EvaluationSuiteResult(
            suite_id="cross-lingual-quality",
            suite_version="v2",
            cases_passed=30,
            cases_total=30,
        ),
    )


def test_registries_are_immutable_unique_and_referentially_valid() -> None:
    assert registry_errors() == []
    assert set(RAG_EXECUTION_PROFILES) == {"economy", "standard", "quality"}
    assert len({profile_hash(value) for value in RAG_EXECUTION_PROFILES.values()}) == 3
    with pytest.raises(TypeError):
        RAG_EXECUTION_PROFILES["other"] = RAG_EXECUTION_PROFILES["standard"]  # type: ignore[index]


def test_candidate_profiles_are_selectable_without_claiming_certification() -> None:
    assert all(
        item.status is CertificationStatus.CANDIDATE for item in PROFILE_CERTIFICATIONS.values()
    )
    assert execution_profile("standard").retrieval_top_k == 10


def test_conflicting_raw_project_values_cannot_alter_preset() -> None:
    resolution = resolve_project_ai_config(
        Settings(),
        _revision(
            {
                "execution": {
                    "profile_id": "economy",
                    "retrieval_top_k": 9,
                }
            }
        ),
        allow_candidate_profiles=True,
    )
    assert resolution.configuration.retrieval.semantic_candidate_top_k == 30
    assert resolution.configuration.retrieval.top_k == 8
    assert resolution.provenance.execution_profile_id == "economy"
    assert resolution.provenance.execution_profile_hash
    assert resolution.provenance.execution_overrides == {}


def test_global_preset_is_authoritative_and_inherited() -> None:
    resolution = resolve_project_ai_config(
        Settings(
            ai_policy={"default_rag_profile": "quality"},
            retrieval={"default_top_k": 2, "semantic_candidate_top_k": 3},
            chat={"max_context_chunks": 2},
        ),
        _revision({"execution": {"profile_id": "inherit"}}),
    )

    assert resolution.configuration.retrieval.top_k == 12
    assert resolution.configuration.retrieval.semantic_candidate_top_k == 80
    assert resolution.configuration.chat.max_context_chunks == 10
    assert resolution.provenance.execution_profile_id == "quality"


def test_every_preset_field_ignores_conflicting_raw_execution_settings() -> None:
    settings = Settings(
        retrieval={
            "default_top_k": 1,
            "semantic_candidate_top_k": 2,
            "keyword_candidate_top_k": 3,
            "hnsw_ef_search": 4,
            "rrf_k": 5,
            "semantic_weight": 2.0,
            "keyword_weight": 3.0,
            "score_threshold": 0.7,
            "rerank_candidate_window": 11,
            "rerank_return_count": 5,
            "rerank_score_threshold": 0.8,
            "min_ocr_confidence": 0.9,
            "max_chunks_per_document": 9,
            "max_chunks_per_section": 8,
            "deduplicate_by_content_hash": False,
            "passage_scoring_enabled": True,
            "passage_window_tokens": 160,
            "passage_overlap_tokens": 40,
            "passage_min_tokens": 64,
            "max_related_sources": 2,
            "max_relationship_candidates": 3,
        },
        chat={
            "max_context_chunks": 2,
            "context_char_budget": 2_000,
            "max_history_messages": 1,
        },
    )

    for profile_id, profile in RAG_EXECUTION_PROFILES.items():
        resolution = resolve_project_ai_config(
            settings,
            _revision({"execution": {"profile_id": profile_id}}),
        )
        assert materialize_execution_values(resolution.configuration) == execution_values(profile)
        assert resolution.configuration.retrieval.rerank_top_n == profile.rerank_candidate_window


def test_global_custom_uses_raw_deployment_execution_values() -> None:
    resolution = resolve_project_ai_config(
        Settings(
            ai_policy={"default_rag_profile": "custom"},
            retrieval={"default_top_k": 7, "semantic_candidate_top_k": 33},
            chat={"max_context_chunks": 5},
        ),
        None,
    )

    assert resolution.configuration.retrieval.top_k == 7
    assert resolution.configuration.retrieval.semantic_candidate_top_k == 33
    assert resolution.configuration.chat.max_context_chunks == 5
    assert resolution.provenance.execution_profile_id == "custom"


def test_project_preset_overrides_global_preset() -> None:
    resolution = resolve_project_ai_config(
        Settings(ai_policy={"default_rag_profile": "economy"}),
        _revision({"execution": {"profile_id": "standard"}}),
    )

    assert resolution.configuration.retrieval.top_k == 10
    assert resolution.provenance.execution_profile_id == "standard"


def test_project_behavior_change_does_not_change_inherited_rag_profile() -> None:
    resolution = resolve_project_ai_config(
        Settings(ai_policy={"default_rag_profile": "economy"}),
        _revision(
            {
                "behavior": {
                    "response_mode": "indexed_only",
                    "translation_policy": "enabled",
                    "domain_instructions": "Use the Project terminology.",
                }
            }
        ),
    )

    assert resolution.provenance.execution_profile_id == "economy"
    assert resolution.configuration.retrieval.top_k == 8
    assert resolution.configuration.retrieval.query_translation_enabled is True


def test_custom_profile_uses_materialized_project_execution_values() -> None:
    resolution = resolve_project_ai_config(
        Settings(ai_policy={"default_rag_profile": "quality"}),
        _revision(
            {
                "execution": {
                    "profile_id": "custom",
                    **profile_registry.execution_values(RAG_EXECUTION_PROFILES["standard"]),
                    "retrieval_top_k": 7,
                }
            }
        ),
    )

    assert resolution.configuration.retrieval.top_k == 7
    assert resolution.configuration.retrieval.semantic_candidate_top_k == 50
    assert resolution.provenance.execution_profile_id == "custom"
    assert resolution.provenance.execution_profile_hash


def test_execution_profile_never_changes_index_artifact_identity() -> None:
    settings = Settings()
    baseline = build_index_artifact_config(settings)
    for profile_id in RAG_EXECUTION_PROFILES:
        resolution = resolve_project_ai_config(
            settings,
            _revision({"execution": {"profile_id": profile_id}}),
            allow_candidate_profiles=True,
        )
        assert resolution.provenance.index_profile_id == "development-hash"
        assert build_index_artifact_config(settings) == baseline
    assert build_index_artifact_config(
        Settings(ai_policy={"default_rag_profile": "quality"})
    ) == baseline
    assert build_index_artifact_config(
        Settings(ai_policy={"default_rag_profile": "custom"})
    ) == baseline


def test_explicit_deployment_profile_adds_index_profile_identity() -> None:
    settings = Settings(runtime={"capability_profile_id": "development"})
    artifact = build_index_artifact_config(settings)
    assert artifact.index_profile_id == "development-hash"
    assert artifact.index_profile_hash


def test_explicit_deployment_profile_rejects_incompatible_provider_wiring() -> None:
    settings = Settings(
        runtime={"capability_profile_id": "hosted-managed"},
        embedding={"backend": "hash"},
    )
    with pytest.raises(ValueError, match="requires embedding backend cohere"):
        validate_profile_compatibility(settings)


def test_explicit_hosted_profile_accepts_exact_index_and_calibration_wiring() -> None:
    settings = Settings(
        runtime={"capability_profile_id": "hosted-managed"},
        llm={"backend": "openai", "model": "gpt-5.6-luna"},
        embedding={"backend": "cohere", "model": "embed-v4.0", "dimensions": 1024},
        retrieval={"embedding_set_version": 3, "reranker_backend": "cohere"},
        ocr={
            "enabled": True,
            "backend": "google_vision",
            "bangla_backend": "google_vision",
        },
    )

    validate_profile_compatibility(settings)


def test_explicit_deployment_profile_rejects_index_profile_drift() -> None:
    settings = Settings(
        runtime={"capability_profile_id": "development"},
        chunking={"target_tokens": 300},
    )
    with pytest.raises(ValueError, match="chunking settings drift"):
        validate_profile_compatibility(settings)


def test_profile_normalization_keeps_complete_nonmatching_custom_values() -> None:
    revision = _revision(
        {
            "execution": {
                "profile_id": "custom",
                **execution_values(RAG_EXECUTION_PROFILES["standard"]),
                "passage_window_tokens": 120,
            }
        }
    )

    result = normalize_v2_project_config(Settings(), revision)

    assert result.base_profile_id is None
    assert result.custom_execution is True
    assert result.configuration.execution.profile_id == "custom"
    assert result.configuration.execution.passage_window_tokens == 120
    assert result.effective_diff == {}


def test_project_preset_provenance_uses_current_definition_hash() -> None:
    revision = _revision(
        {
            "execution": {
                "profile_id": "standard",
            }
        }
    )

    resolution = resolve_project_ai_config(
        Settings(), revision, allow_candidate_profiles=True
    )
    assert resolution.configuration.retrieval.top_k == 10
    assert resolution.provenance.execution_profile_hash == profile_hash(
        RAG_EXECUTION_PROFILES["standard"]
    )


def test_profile_change_only_changes_new_effective_snapshots() -> None:
    standard = resolve_project_ai_config(
        Settings(ai_policy={"default_rag_profile": "standard"}), None
    ).secret_free_snapshot()
    quality = resolve_project_ai_config(
        Settings(ai_policy={"default_rag_profile": "quality"}), None
    ).secret_free_snapshot()

    assert standard["provenance"]["execution_profile_id"] == "standard"
    assert standard["configuration"]["retrieval"]["top_k"] == 10
    assert quality["provenance"]["execution_profile_id"] == "quality"
    assert quality["configuration"]["retrieval"]["top_k"] == 12


def test_certification_gate_consumes_manifest_requirements_without_tax_assumptions() -> None:
    generic_manifest = ProfileCertificationManifest(
        manifest_id="private-domain",
        requirements=(
            EvaluationSuiteRequirement(
                suite_id="private-domain-grounding",
                suite_version="2026-09",
                minimum_cases=5,
            ),
        ),
    )
    passed = evaluate_profile_candidate(
        ProfileCertificationInput(
            profile_id="standard",
            manifest=generic_manifest,
            suite_results=(
                EvaluationSuiteResult(
                    suite_id="private-domain-grounding",
                    suite_version="2026-09",
                    cases_passed=5,
                    cases_total=5,
                ),
            ),
            baseline=_metrics(),
            candidate=_metrics(),
        )
    )
    assert passed.passed is True


def test_hosted_certification_manifest_requires_tax_and_other_named_suites() -> None:
    failed = evaluate_profile_candidate(
        ProfileCertificationInput(
            profile_id="standard",
            manifest=HOSTED_RAG_CERTIFICATION_MANIFEST,
            suite_results=_hosted_suite_results(tax_passed=20)[:1],
            baseline=_metrics(),
            candidate=_metrics(),
        )
    )
    assert failed.passed is False
    assert "suite_gate:tax@v1" in failed.failures
    assert "suite_missing:ci-smoke@v1" in failed.failures
    assert "suite_missing:cross-lingual-quality@v2" in failed.failures


@pytest.mark.parametrize(
    ("profile_id", "candidate"),
    [
        ("standard", _metrics()),
        ("economy", _metrics(p95_latency_ms=80.0)),
        ("quality", _metrics(recall_at_k=0.92, p95_latency_ms=120.0)),
    ],
)
def test_each_seed_profile_family_has_a_passing_certification_path(
    profile_id: str,
    candidate: ProfileMetrics,
) -> None:
    result = evaluate_profile_candidate(
        ProfileCertificationInput(
            profile_id=profile_id,
            manifest=HOSTED_RAG_CERTIFICATION_MANIFEST,
            suite_results=_hosted_suite_results(),
            baseline=_metrics(),
            candidate=candidate,
        )
    )

    assert result.passed is True
