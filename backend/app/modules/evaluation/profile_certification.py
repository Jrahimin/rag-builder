"""Pure certification gate for immutable RAG execution profile candidates."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.platform.config.profiles import PROFILE_CERTIFICATIONS, RAG_EXECUTION_PROFILES


class ProfileMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recall_at_k: float = Field(ge=0.0, le=1.0)
    ndcg: float = Field(ge=0.0, le=1.0)
    false_accept_rate: float = Field(ge=0.0, le=1.0)
    false_refusal_rate: float = Field(ge=0.0, le=1.0)
    groundedness: float = Field(ge=0.0, le=1.0)
    citation_coverage: float = Field(ge=0.0, le=1.0)
    p95_latency_ms: float = Field(ge=0.0)
    provider_cost: float | None = Field(default=None, ge=0.0)


class ProfileCertificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    tax_cases_passed: int = Field(ge=0)
    tax_cases_total: int = Field(ge=1)
    non_tax_suites: tuple[str, ...]
    baseline: ProfileMetrics
    candidate: ProfileMetrics
    maximum_metric_regression: float = Field(default=0.02, ge=0.0, le=1.0)
    maximum_latency_ratio: float = Field(default=1.5, ge=1.0)
    minimum_quality_gain: float = Field(default=0.01, ge=0.0, le=1.0)


class ProfileCertificationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile_id: str
    passed: bool
    failures: tuple[str, ...]
    tax_score: str
    non_tax_suites: tuple[str, ...]


def evaluate_profile_candidate(
    evaluation: ProfileCertificationInput,
) -> ProfileCertificationResult:
    """Evaluate a candidate without mutating the code-owned certification registry."""
    if evaluation.profile_id not in RAG_EXECUTION_PROFILES:
        raise ValueError(f"unknown RAG execution profile: {evaluation.profile_id}")
    failures: list[str] = []
    candidate = evaluation.candidate
    baseline = evaluation.baseline
    if evaluation.tax_cases_passed != evaluation.tax_cases_total:
        failures.append("tax_regression")
    if evaluation.tax_cases_total != 21:
        failures.append("tax_v1_case_count_mismatch")
    if not evaluation.non_tax_suites:
        failures.append("non_tax_coverage_missing")
    if candidate.false_accept_rate > baseline.false_accept_rate:
        failures.append("false_accept_regression")
    if candidate.false_accept_rate > 0.0:
        failures.append("false_accept_gate")
    if candidate.groundedness < 0.80:
        failures.append("groundedness_gate")
    if candidate.citation_coverage < 0.80:
        failures.append("citation_gate")
    tolerance = evaluation.maximum_metric_regression
    for name in ("recall_at_k", "ndcg"):
        if getattr(candidate, name) + tolerance < getattr(baseline, name):
            failures.append(f"{name}_non_inferiority")
    if candidate.false_refusal_rate > baseline.false_refusal_rate + tolerance:
        failures.append("false_refusal_non_inferiority")

    family = evaluation.profile_id.split("@", maxsplit=1)[0]
    if family == "economy":
        cost_improved = (
            candidate.provider_cost is not None
            and baseline.provider_cost is not None
            and candidate.provider_cost < baseline.provider_cost
        )
        if candidate.p95_latency_ms >= baseline.p95_latency_ms and not cost_improved:
            failures.append("economy_cost_or_latency_gain")
    elif family == "quality":
        primary_gain = max(
            candidate.recall_at_k - baseline.recall_at_k,
            candidate.ndcg - baseline.ndcg,
        )
        if primary_gain < evaluation.minimum_quality_gain:
            failures.append("quality_primary_metric_gain")
        if candidate.p95_latency_ms > baseline.p95_latency_ms * evaluation.maximum_latency_ratio:
            failures.append("quality_latency_budget")
    elif family != "standard":
        failures.append("unsupported_profile_family")

    # The seed registry remains candidate until the produced result is reviewed
    # and checked into code as a new immutable certification manifest.
    assert PROFILE_CERTIFICATIONS[evaluation.profile_id].status.value == "candidate"
    return ProfileCertificationResult(
        profile_id=evaluation.profile_id,
        passed=not failures,
        failures=tuple(dict.fromkeys(failures)),
        tax_score=f"{evaluation.tax_cases_passed}/{evaluation.tax_cases_total}",
        non_tax_suites=evaluation.non_tax_suites,
    )
