"""Temporal and independent configuration contracts for the reliability rollout."""

import uuid
from datetime import UTC, datetime

import pytest

from app.core.config import Settings
from app.modules.conversations.turn_resolution import normalize_request_scope
from app.platform.config.profiles import RAG_EXECUTION_PROFILES, execution_values
from app.platform.config.project_ai import ConfigRevisionRecord, resolve_project_ai_config
from app.platform.domain.language_detection import (
    detect_query_language_profile,
    missing_protected_literals,
    select_translation_target,
)
from app.platform.domain.request_scope import ProjectCalendarPolicy, request_scope_hash
from app.platform.providers.request_work import RequestWork

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("profile", ["inherit", "economy", "standard", "quality", "custom"])
def test_execution_policy_survives_every_explicit_profile(profile):
    record = ConfigRevisionRecord(
        id=uuid.uuid4(),
        revision_number=7,
        configuration_hash="a" * 64,
        configuration={
            "execution": {
                **(
                    execution_values(RAG_EXECUTION_PROFILES["standard"])
                    if profile == "custom"
                    else {}
                ),
                "profile_id": profile,
                "execution_policy": "bounded_extended_v2",
            }
        },
        schema_version=2,
    )
    resolved = resolve_project_ai_config(Settings(), record)
    assert resolved.configuration.chat.execution_policy == "bounded_extended_v2"
    assert resolved.provenance.execution_profile_id == (
        "standard" if profile == "inherit" else profile
    )


def calendar():
    return ProjectCalendarPolicy(
        timezone="Asia/Dhaka", period_kind="assessment", boundary_month=7, boundary_day=1
    )


@pytest.mark.parametrize(
    "instant,year", [("2026-06-30T17:59:00+00:00", 2025), ("2026-06-30T18:00:00+00:00", 2026)]
)
def test_calendar_uses_local_boundary_without_source_recency(instant, year):
    scope = normalize_request_scope(
        "What is the current assessment year threshold?",
        reference_time=datetime.fromisoformat(instant),
        calendar_policy=calendar(),
    )
    assert scope.requested_periods[0].start_year == year
    assert scope.period_origin == "project_calendar"
    explicit = normalize_request_scope(
        "For AY 2030-31 show the current threshold.",
        reference_time=datetime.fromisoformat(instant),
        calendar_policy=calendar(),
    )
    assert explicit.requested_periods[0].start_year == 2030
    assert explicit.period_origin == "explicit"
    assert request_scope_hash(scope) != request_scope_hash(explicit)


def test_period_kind_required_and_ambiguous_period_is_explicit():
    now = datetime(2026, 10, 1, tzinfo=UTC)
    generic = normalize_request_scope(
        "What is the current rule?", reference_time=now, calendar_policy=calendar()
    )
    assert not generic.requested_periods
    ambiguous = normalize_request_scope(
        "What is the current assessment year threshold?", reference_time=now
    )
    assert ambiguous.period_ambiguous and not ambiguous.requested_periods


@pytest.mark.parametrize(
    "original,translated,valid",
    [
        ("15 days under section 21(2)", "১৫ দিন ধারা ২১(2)", True),
        ("15 days", "১৫ মাস", False),
        ("2026-07-01", "2026-01-07", False),
        ("AY 2026-27", "AY 27-2026", False),
        ("section 21(2)", "ধারা 21(3)", False),
    ],
)
def test_typed_translation_literals(original, translated, valid):
    assert (not missing_protected_literals(original, translated)) is valid


def test_romanized_bangla_can_reach_bangla_only_corpus():
    profile = detect_query_language_profile("amar jonno ki niyom hobe")
    assert profile.is_romanized_or_codeswitched
    assert select_translation_target(profile, {"bn": 20}) == "bn"


@pytest.mark.parametrize(
    "policy,cutoffs",
    [("bounded_v2", (30, 40, 58, 60)), ("bounded_extended_v2", (90, 100, 118, 120))],
)
@pytest.mark.parametrize("started", [402.839583442, 1000.0])
def test_absolute_deadlines_never_expand_with_measured_latency(policy, cutoffs, started):
    work = RequestWork(uuid.uuid4())
    work.started = started
    work.configure_execution(policy, estimates={"verification": {"samples": 25, "p95_seconds": 40}})
    actual = tuple(
        work.phase_deadline(purpose)
        for purpose in ("coverage_review", "answer_generation", "claim_verification", "persistence")
    )
    assert actual == pytest.approx(
        tuple(work.started + seconds for seconds in cutoffs), rel=0, abs=1e-9
    )
    work.complete_stage("coverage")
    assert work.recovery_deadline == pytest.approx(work.started + cutoffs[0], rel=0, abs=1e-9)
    work.configure_execution("bounded_extended_v2")
    assert work.request_deadline == pytest.approx(work.started + cutoffs[3], rel=0, abs=1e-9)


def test_frozen_measured_throughput_sizes_output_without_extending_cutoff(monkeypatch):
    import app.platform.providers.request_work as module

    monkeypatch.setattr(module.time, "perf_counter", lambda: 10000.0)
    work = RequestWork(uuid.uuid4())
    work.configure_execution("bounded_v2")
    samples = [
        {
            "message_id": str(uuid.uuid4()),
            "metadata": {
                "lifecycle": {
                    "deadline": {"budget_class": "standard"},
                    "provider_calls": [
                        {
                            "kind": "llm",
                            "purpose": "answer_generation",
                            "status": "completed",
                            "output_tokens": 100,
                            "duration_ms": 2000,
                        }
                    ],
                }
            },
        }
        for _ in range(20)
    ]
    work.freeze_measured_stages(samples)
    assert work.output_tokens_per_second == 50
    monkeypatch.setattr(module.time, "perf_counter", lambda: work.started + 35)
    with work.purpose("answer_generation"):
        assert work.output_token_budget(1000) == 250
    assert work.phase_deadline("answer_generation") == work.started + 40
    work.freeze_measured_stages([])
    assert work.output_tokens_per_second == 50
