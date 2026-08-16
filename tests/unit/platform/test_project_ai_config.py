"""Phase 1 Project policy, capability, and snapshot regression tests."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import Settings
from app.core.exceptions import BadRequestError
from app.platform.config.project_ai import (
    ConfigRevisionRecord,
    ProjectAIConfig,
    apply_effective_ai_config,
    resolve_project_ai_config,
    stable_hash,
)
from app.platform.jobs.configuration import build_job_configuration
from app.platform.providers.capabilities import (
    CAPABILITY_VERSION,
    describe_llm_capability,
    translate_generation_parameters,
)

pytestmark = pytest.mark.unit


def _revision(payload: dict[str, object]) -> ConfigRevisionRecord:
    return ConfigRevisionRecord(
        id=uuid.uuid4(),
        revision_number=3,
        configuration_hash=stable_hash(payload),
        configuration=payload,
    )


def test_projects_without_revision_retain_global_behavior() -> None:
    settings = Settings(
        llm={"backend": "echo", "model": "global-model", "max_tokens": 321},
        retrieval={"strategy": "semantic", "default_top_k": 7},
    )

    resolution = resolve_project_ai_config(settings, None)

    assert resolution.configuration.llm.model == "global-model"
    assert resolution.configuration.llm.max_tokens == 321
    assert resolution.configuration.retrieval.strategy.value == "semantic"
    assert resolution.configuration.retrieval.top_k == 7
    assert resolution.provenance.project_config_revision_id is None
    assert resolution.provenance.provider_capability_version == CAPABILITY_VERSION


def test_project_revision_is_typed_hashed_and_has_origins() -> None:
    payload = ProjectAIConfig.model_validate(
        {
            "llm": {"model": "project-model", "temperature": 0.4},
            "retrieval": {"top_k": 12},
            "domain_instructions": "Answer for Acme support.",
            "source_policy_mode": "observe",
        }
    ).model_dump(mode="json", exclude_none=True)
    revision = _revision(payload)

    resolution = resolve_project_ai_config(Settings(), revision)

    assert resolution.configuration.llm.model == "project-model"
    assert resolution.configuration.retrieval.top_k == 12
    assert resolution.configuration.source_policy_mode.value == "observe"
    assert resolution.origins["llm.model"] == "project"
    assert resolution.origins["llm.provider"] == "global"
    assert resolution.provenance.project_config_revision_id == revision.id
    assert resolution.provenance.project_config_hash == revision.configuration_hash


def test_deployment_source_policy_cap_lowers_effective_mode_without_mutating_revision() -> None:
    revision = _revision({"source_policy_mode": "enforce"})

    resolution = resolve_project_ai_config(
        Settings(ai_policy={"source_policy_deployment_cap": "observe"}),
        revision,
    )

    assert revision.configuration["source_policy_mode"] == "enforce"
    assert resolution.configuration.source_policy_mode.value == "observe"
    assert resolution.origins["source_policy_mode"] == "deployment_safety_cap"
    assert resolution.provenance.configured_source_policy_mode.value == "enforce"
    assert resolution.provenance.effective_source_policy_mode.value == "observe"
    assert resolution.provenance.source_policy_deployment_cap.value == "observe"


def test_compatibility_mode_records_and_applies_deprecated_overrides() -> None:
    resolution = resolve_project_ai_config(
        Settings(),
        None,
        deprecated_overrides={"model": "legacy-model", "temperature": 0.7},
    )

    assert resolution.configuration.llm.model == "legacy-model"
    assert resolution.compatibility_diagnostics == ["model", "temperature"]
    assert resolution.origins["llm.model"] == "deprecated_request_compatibility"


def test_strict_mode_rejects_even_explicit_null_deprecated_override() -> None:
    settings = Settings(ai_policy={"request_override_mode": "strict"})

    with pytest.raises(BadRequestError) as caught:
        resolve_project_ai_config(
            settings,
            None,
            deprecated_overrides={"temperature": None},
        )

    assert caught.value.code == "request_policy_override_forbidden"


def test_provider_capability_safely_omits_global_unsupported_temperature() -> None:
    settings = Settings(
        llm={
            "backend": "openai",
            "model": "o3-mini",
            "temperature": 0.5,
            "openai_api_key": "live-only",
        }
    )

    resolution = resolve_project_ai_config(settings, None)

    assert resolution.configuration.llm.temperature is None
    assert resolution.origins["llm.temperature"] == "provider_safe_omission"


def test_explicit_unsupported_project_parameter_is_rejected() -> None:
    revision = _revision(
        {
            "llm": {
                "provider": "openai",
                "model": "o3-mini",
                "temperature": 0.5,
            }
        }
    )

    with pytest.raises(BadRequestError) as caught:
        resolve_project_ai_config(Settings(), revision)

    assert caught.value.code == "unsupported_provider_parameter"


def test_parameter_translation_uses_vendor_token_limit_names() -> None:
    openai = describe_llm_capability("openai", "gpt-4o-mini")
    gemini = describe_llm_capability("gemini", "gemini-2.0-flash")
    ollama = describe_llm_capability("ollama", "llama3")

    assert translate_generation_parameters(openai, temperature=0.2, max_tokens=10) == {
        "temperature": 0.2,
        "max_completion_tokens": 10,
    }
    assert translate_generation_parameters(gemini, temperature=None, max_tokens=11) == {
        "maxOutputTokens": 11
    }
    assert translate_generation_parameters(ollama, temperature=0.1, max_tokens=12) == {
        "temperature": 0.1,
        "num_predict": 12,
    }


def test_job_snapshot_captures_project_policy_provenance_and_no_secrets() -> None:
    settings = Settings(
        llm={"openai_api_key": "never-store", "model": "global"},
    )
    revision = _revision({"llm": {"model": "project-model", "max_tokens": 99}})
    resolution = resolve_project_ai_config(settings, revision)

    snapshot = build_job_configuration(
        settings,
        resolution=resolution,
        active_index_build_id=str(uuid.uuid4()),
        source_metadata_generation=8,
    )
    restored = apply_effective_ai_config(settings, resolution)

    assert snapshot.schema_version == 3
    assert snapshot.quality["llm"]["model"] == "project-model"
    assert snapshot.provenance["project_config_revision_id"] == str(revision.id)
    assert snapshot.provenance["source_metadata_generation"] == 8
    assert "never-store" not in str(snapshot.model_dump())
    assert restored.llm.model == "project-model"


def test_index_hash_ignores_project_chat_llm_and_top_k_policy() -> None:
    settings = Settings(retrieval={"default_top_k": 7})
    first = build_job_configuration(
        settings,
        resolution=resolve_project_ai_config(
            settings,
            _revision(
                {
                    "llm": {"model": "first-model", "max_tokens": 100},
                    "chat": {"max_history_messages": 2},
                    "retrieval": {"top_k": 3},
                }
            ),
        ),
    )
    second = build_job_configuration(
        settings,
        resolution=resolve_project_ai_config(
            settings,
            _revision(
                {
                    "llm": {"model": "second-model", "max_tokens": 200},
                    "chat": {"max_history_messages": 9},
                    "retrieval": {"top_k": 25},
                }
            ),
        ),
    )

    assert first.index["retrieval"]["default_top_k"] == 7
    assert second.index["retrieval"]["default_top_k"] == 7
    assert first.index_output_digest() == second.index_output_digest()
    assert first.output_digest() != second.output_digest()
