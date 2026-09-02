"""Pure certification gate for code-owned RAG execution profile candidates."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.platform.config.profiles import RAG_EXECUTION_PROFILES


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


class EvaluationSuiteRequirement(BaseModel):
    """One named, versioned suite required by a certification manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    suite_id: str = Field(min_length=1, max_length=128)
    suite_version: str = Field(min_length=1, max_length=64)
    minimum_cases: int = Field(default=1, ge=1)
    minimum_pass_rate: float = Field(default=1.0, gt=0.0, le=1.0)


class EvaluationSuiteResult(BaseModel):
    """Observed result for one exact suite identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    suite_id: str = Field(min_length=1, max_length=128)
    suite_version: str = Field(min_length=1, max_length=64)
    cases_passed: int = Field(ge=0)
    cases_total: int = Field(ge=1)
    artifact_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def _validate_case_counts(self) -> EvaluationSuiteResult:
        if self.cases_passed > self.cases_total:
            raise ValueError("cases_passed must not exceed cases_total")
        return self


class ProfileCertificationManifest(BaseModel):
    """Reusable requirements for a certification target such as hosted production."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_id: str = Field(min_length=1, max_length=128)
    requirements: tuple[EvaluationSuiteRequirement, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_unique_requirements(self) -> ProfileCertificationManifest:
        identities = [
            (requirement.suite_id, requirement.suite_version) for requirement in self.requirements
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("certification suite requirements must be unique")
        return self


# Tax is a hosted-product requirement, not an assumption in the reusable gate.
HOSTED_RAG_CERTIFICATION_MANIFEST = ProfileCertificationManifest(
    manifest_id="hosted-rag-production",
    requirements=(
        EvaluationSuiteRequirement(
            suite_id="tax",
            suite_version="v1",
            minimum_cases=21,
            minimum_pass_rate=1.0,
        ),
        EvaluationSuiteRequirement(suite_id="ci-smoke", suite_version="v1"),
        EvaluationSuiteRequirement(suite_id="cross-lingual-quality", suite_version="v2"),
    ),
)


class ProfileCertificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    manifest: ProfileCertificationManifest
    suite_results: tuple[EvaluationSuiteResult, ...]
    baseline: ProfileMetrics
    candidate: ProfileMetrics
    maximum_metric_regression: float = Field(default=0.02, ge=0.0, le=1.0)
    maximum_latency_ratio: float = Field(default=1.5, ge=1.0)
    minimum_quality_gain: float = Field(default=0.01, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_unique_results(self) -> ProfileCertificationInput:
        identities = [(result.suite_id, result.suite_version) for result in self.suite_results]
        if len(identities) != len(set(identities)):
            raise ValueError("evaluation suite results must be unique")
        return self


class ProfileCertificationSuiteOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    suite_id: str
    suite_version: str
    score: str | None
    passed: bool


class ProfileCertificationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile_id: str
    manifest_id: str
    passed: bool
    failures: tuple[str, ...]
    suite_outcomes: tuple[ProfileCertificationSuiteOutcome, ...]


def evaluate_profile_candidate(
    evaluation: ProfileCertificationInput,
) -> ProfileCertificationResult:
    """Evaluate a candidate without mutating the code-owned profile registry."""
    if evaluation.profile_id not in RAG_EXECUTION_PROFILES:
        raise ValueError(f"unknown RAG execution profile: {evaluation.profile_id}")

    failures: list[str] = []
    results = {
        (result.suite_id, result.suite_version): result for result in evaluation.suite_results
    }
    suite_outcomes: list[ProfileCertificationSuiteOutcome] = []
    for requirement in evaluation.manifest.requirements:
        identity = (requirement.suite_id, requirement.suite_version)
        label = f"{requirement.suite_id}@{requirement.suite_version}"
        observed = results.get(identity)
        if observed is None:
            failures.append(f"suite_missing:{label}")
            suite_outcomes.append(
                ProfileCertificationSuiteOutcome(
                    suite_id=requirement.suite_id,
                    suite_version=requirement.suite_version,
                    score=None,
                    passed=False,
                )
            )
            continue
        enough_cases = observed.cases_total >= requirement.minimum_cases
        pass_rate = observed.cases_passed / observed.cases_total
        suite_passed = enough_cases and pass_rate >= requirement.minimum_pass_rate
        if not enough_cases:
            failures.append(f"suite_case_count:{label}")
        if pass_rate < requirement.minimum_pass_rate:
            failures.append(f"suite_gate:{label}")
        suite_outcomes.append(
            ProfileCertificationSuiteOutcome(
                suite_id=requirement.suite_id,
                suite_version=requirement.suite_version,
                score=f"{observed.cases_passed}/{observed.cases_total}",
                passed=suite_passed,
            )
        )

    candidate = evaluation.candidate
    baseline = evaluation.baseline
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

    if evaluation.profile_id == "economy":
        cost_improved = (
            candidate.provider_cost is not None
            and baseline.provider_cost is not None
            and candidate.provider_cost < baseline.provider_cost
        )
        if candidate.p95_latency_ms >= baseline.p95_latency_ms and not cost_improved:
            failures.append("economy_cost_or_latency_gain")
    elif evaluation.profile_id == "quality":
        primary_gain = max(
            candidate.recall_at_k - baseline.recall_at_k,
            candidate.ndcg - baseline.ndcg,
        )
        if primary_gain < evaluation.minimum_quality_gain:
            failures.append("quality_primary_metric_gain")
        if candidate.p95_latency_ms > baseline.p95_latency_ms * evaluation.maximum_latency_ratio:
            failures.append("quality_latency_budget")
    elif evaluation.profile_id != "standard":
        failures.append("unsupported_profile_family")

    return ProfileCertificationResult(
        profile_id=evaluation.profile_id,
        manifest_id=evaluation.manifest.manifest_id,
        passed=not failures,
        failures=tuple(dict.fromkeys(failures)),
        suite_outcomes=tuple(suite_outcomes),
    )
