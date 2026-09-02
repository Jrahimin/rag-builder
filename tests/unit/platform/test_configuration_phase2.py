"""Phase 2 immutable profile, resolver, index identity, and certification tests."""

from __future__ import annotations

import uuid
from types import MappingProxyType

import pytest

import app.platform.config.profiles as profile_registry
from app.core.config import Settings
from app.core.exceptions import BadRequestError
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


def test_seed_profiles_remain_candidates_and_cannot_be_normal_project_defaults() -> None:
    assert all(
        item.status is CertificationStatus.CANDIDATE for item in PROFILE_CERTIFICATIONS.values()
    )
    with pytest.raises(Exception, match="not certified"):
        execution_profile("standard")
    assert execution_profile("standard", allow_candidate=True).retrieval_top_k == 10


def test_test_lab_candidate_resolution_records_profile_hash_and_sparse_override() -> None:
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
    assert resolution.configuration.retrieval.top_k == 9
    assert resolution.provenance.execution_profile_id == "economy"
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
        assert resolution.provenance.index_profile_id == "development-hash"
        assert build_index_artifact_config(settings) == baseline


def test_explicit_deployment_profile_adds_index_profile_identity() -> None:
    settings = Settings(runtime={"capability_profile_id": "development"})
    artifact = build_index_artifact_config(settings)
    assert artifact.index_profile_id == "development-hash"
    assert artifact.index_profile_hash
    assert DEPLOYMENT_CAPABILITY_PROFILES["development"].default_rag_profile_id is None


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


def test_profile_normalization_retains_sparse_advanced_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    certifications = {
        profile_id: ProfileCertification(
            profile_id=profile_id,
            status=(
                CertificationStatus.CERTIFIED
                if profile_id == "standard"
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

    assert result.base_profile_id == "standard"
    assert result.custom_execution is True
    assert result.configuration.execution.profile_id == "standard"
    assert result.configuration.execution.profile_hash == profile_hash(
        RAG_EXECUTION_PROFILES["standard"]
    )
    assert result.configuration.execution.passage_window_tokens == 120
    assert result.effective_diff == {}


def test_pinned_execution_profile_hash_rejects_definition_drift() -> None:
    revision = _revision(
        {
            "execution": {
                "profile_id": "standard",
                "profile_hash": "0" * 64,
            }
        }
    )

    with pytest.raises(BadRequestError, match="no longer matches") as error:
        resolve_project_ai_config(Settings(), revision, allow_candidate_profiles=True)

    assert error.value.code == "execution_profile_hash_mismatch"


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
