"""Phase 2 immutable profile, resolver, index identity, and certification tests."""

from __future__ import annotations

import uuid
from types import MappingProxyType

import pytest

import app.platform.config.profiles as profile_registry
from app.core.config import Settings
from app.core.exceptions import BadRequestError
from app.modules.evaluation.profile_certification import (
    ProfileCertificationInput,
    ProfileMetrics,
    evaluate_profile_candidate,
)
from app.platform.config.index_artifact import build_index_artifact_config
from app.platform.config.profiles import (
    DEPLOYMENT_CAPABILITY_PROFILES,
    PROFILE_CERTIFICATIONS,
    RAG_EXECUTION_PROFILES,
    CertificationStatus,
    ProfileCertification,
    execution_profile,
    profile_hash,
    registry_errors,
    validate_profile_compatibility,
)
from app.platform.config.project_ai import (
    ConfigRevisionRecord,
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


def test_registries_are_immutable_unique_and_referentially_valid() -> None:
    assert registry_errors() == []
    assert set(RAG_EXECUTION_PROFILES) == {"economy@v1", "standard@v1", "quality@v1"}
    assert len({profile_hash(value) for value in RAG_EXECUTION_PROFILES.values()}) == 3
    with pytest.raises(TypeError):
        RAG_EXECUTION_PROFILES["other@v1"] = RAG_EXECUTION_PROFILES["standard@v1"]  # type: ignore[index]


def test_seed_profiles_remain_candidates_and_cannot_be_normal_project_defaults() -> None:
    assert all(
        item.status is CertificationStatus.CANDIDATE for item in PROFILE_CERTIFICATIONS.values()
    )
    with pytest.raises(Exception, match="not certified"):
        execution_profile("standard@v1")
    assert execution_profile("standard@v1", allow_candidate=True).retrieval_top_k == 10


def test_test_lab_candidate_resolution_records_profile_hash_and_sparse_override() -> None:
    resolution = resolve_project_ai_config(
        Settings(),
        _revision(
            {
                "execution": {
                    "profile_id": "economy@v1",
                    "retrieval_top_k": 9,
                }
            }
        ),
        allow_candidate_profiles=True,
    )
    assert resolution.configuration.retrieval.semantic_candidate_top_k == 30
    assert resolution.configuration.retrieval.top_k == 9
    assert resolution.provenance.execution_profile_id == "economy@v1"
    assert resolution.provenance.execution_profile_hash
    assert resolution.provenance.execution_overrides == {"retrieval_top_k": 9}


def test_execution_profile_never_changes_index_artifact_identity() -> None:
    settings = Settings()
    baseline = build_index_artifact_config(settings)
    for profile_id in RAG_EXECUTION_PROFILES:
        resolution = resolve_project_ai_config(
            settings,
            _revision({"execution": {"profile_id": profile_id}}),
            allow_candidate_profiles=True,
        )
        assert resolution.provenance.index_profile_id == "development-hash@v1"
        assert build_index_artifact_config(settings) == baseline


def test_explicit_deployment_profile_adds_index_profile_identity() -> None:
    settings = Settings(runtime={"capability_profile_id": "development@v1"})
    artifact = build_index_artifact_config(settings)
    assert artifact.index_profile_id == "development-hash@v1"
    assert artifact.index_profile_hash
    assert DEPLOYMENT_CAPABILITY_PROFILES["development@v1"].default_rag_profile_id is None


def test_explicit_deployment_profile_rejects_incompatible_provider_wiring() -> None:
    settings = Settings(
        runtime={"capability_profile_id": "hosted-managed@v1"},
        embedding={"backend": "hash"},
    )
    with pytest.raises(ValueError, match="requires embedding backend cohere"):
        validate_profile_compatibility(settings)


def test_explicit_hosted_profile_accepts_exact_index_and_calibration_wiring() -> None:
    settings = Settings(
        runtime={"capability_profile_id": "hosted-managed@v1"},
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
        runtime={"capability_profile_id": "development@v1"},
        chunking={"target_tokens": 300},
    )
    with pytest.raises(ValueError, match="chunking settings drift"):
        validate_profile_compatibility(settings)


def test_profile_normalization_retains_sparse_advanced_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    certifications = {
        profile_id: ProfileCertification(
            profile_id=profile_id,
            status=(
                CertificationStatus.CERTIFIED
                if profile_id == "standard@v1"
                else CertificationStatus.CANDIDATE
            ),
        )
        for profile_id in RAG_EXECUTION_PROFILES
    }
    monkeypatch.setattr(
        profile_registry,
        "PROFILE_CERTIFICATIONS",
        MappingProxyType(certifications),
    )
    revision = _revision(
        {
            "execution": {
                "retrieval_top_k": 10,
                "semantic_candidate_top_k": 50,
                "keyword_candidate_top_k": 50,
                "hnsw_ef_search": 100,
                "rrf_k": 60,
                "semantic_weight": 1.0,
                "keyword_weight": 1.0,
                "rerank_mode": "always",
                "rerank_candidate_window": 25,
                "rerank_return_count": 8,
                "max_context_chunks": 8,
                "context_char_budget": 12_000,
                "max_history_messages": 20,
                "passage_window_tokens": 120,
            }
        }
    )

    result = normalize_v2_project_config(Settings(), revision)

    assert result.base_profile_id == "standard@v1"
    assert result.custom_execution is True
    assert result.configuration.execution.profile_id == "standard@v1"
    assert result.configuration.execution.profile_hash == profile_hash(
        RAG_EXECUTION_PROFILES["standard@v1"]
    )
    assert result.configuration.execution.passage_window_tokens == 120
    assert result.effective_diff == {}


def test_pinned_execution_profile_hash_rejects_definition_drift() -> None:
    revision = _revision(
        {
            "execution": {
                "profile_id": "standard@v1",
                "profile_hash": "0" * 64,
            }
        }
    )

    with pytest.raises(BadRequestError, match="no longer matches") as error:
        resolve_project_ai_config(Settings(), revision, allow_candidate_profiles=True)

    assert error.value.code == "execution_profile_hash_mismatch"


def test_certification_gate_requires_tax_and_non_tax_coverage() -> None:
    passed = evaluate_profile_candidate(
        ProfileCertificationInput(
            profile_id="standard@v1",
            tax_cases_passed=21,
            tax_cases_total=21,
            non_tax_suites=("ci_smoke_v1", "cross_lingual_quality_v2"),
            baseline=_metrics(),
            candidate=_metrics(),
        )
    )
    assert passed.passed is True
    failed = evaluate_profile_candidate(
        ProfileCertificationInput(
            profile_id="quality@v1",
            tax_cases_passed=20,
            tax_cases_total=21,
            non_tax_suites=(),
            baseline=_metrics(),
            candidate=_metrics(),
        )
    )
    assert failed.passed is False
    assert "tax_regression" in failed.failures
    assert "non_tax_coverage_missing" in failed.failures


@pytest.mark.parametrize(
    ("profile_id", "candidate"),
    [
        ("standard@v1", _metrics()),
        ("economy@v1", _metrics(p95_latency_ms=80.0)),
        ("quality@v1", _metrics(recall_at_k=0.92, p95_latency_ms=120.0)),
    ],
)
def test_each_seed_profile_family_has_a_passing_certification_path(
    profile_id: str,
    candidate: ProfileMetrics,
) -> None:
    result = evaluate_profile_candidate(
        ProfileCertificationInput(
            profile_id=profile_id,
            tax_cases_passed=21,
            tax_cases_total=21,
            non_tax_suites=("ci_smoke_v1", "cross_lingual_quality_v2"),
            baseline=_metrics(),
            candidate=candidate,
        )
    )

    assert result.passed is True
