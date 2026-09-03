"""Canonical V2 Project policy and effective snapshot regression tests."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import Settings
from app.core.exceptions import BadRequestError
from app.platform.config.profiles import RAG_EXECUTION_PROFILES, execution_values
from app.platform.config.project_ai import (
    ConfigRevisionRecord,
    apply_effective_ai_config,
    resolve_project_ai_config,
    stable_hash,
)
from app.platform.jobs.configuration import build_job_configuration
from app.platform.providers.capabilities import CAPABILITY_VERSION

pytestmark = pytest.mark.unit


def _revision(payload: dict[str, object]) -> ConfigRevisionRecord:
    return ConfigRevisionRecord(
        id=uuid.uuid4(),
        revision_number=3,
        configuration_hash=stable_hash(payload),
        configuration=payload,
        schema_version=2,
    )


def test_projects_without_revision_use_canonical_v2_invariants() -> None:
    settings = Settings(
        llm={"backend": "echo", "model": "global-model", "max_tokens": 321},
        retrieval={"strategy": "semantic", "default_top_k": 7},
    )

    resolution = resolve_project_ai_config(settings, None)

    assert resolution.configuration.llm.model == "global-model"
    assert resolution.configuration.llm.max_tokens == 321
    assert resolution.configuration.retrieval.strategy.value == "hybrid"
    assert resolution.provenance.project_config_revision_id is None
    assert resolution.provenance.provider_capability_version == CAPABILITY_VERSION


def test_project_behavior_uses_allowlisted_generation_model_and_translation() -> None:
    settings = Settings(
        ai_policy={
            "allowed_generation_model_ids": [
                "deployment-default",
                "openai-gpt-4o-mini",
            ]
        }
    )
    revision = _revision(
        {
            "behavior": {
                "generation_model_id": "openai-gpt-4o-mini",
                "translation_policy": "enabled",
                "domain_instructions": "Use the Project terminology.",
            }
        }
    )

    resolution = resolve_project_ai_config(settings, revision)

    assert resolution.configuration.llm.model == "gpt-4o-mini"
    assert resolution.configuration.retrieval.query_translation_enabled is True
    assert resolution.origins["llm.generation_model_id"] == "project"


def test_unknown_generation_model_is_rejected() -> None:
    revision = _revision({"behavior": {"generation_model_id": "openai/arbitrary-model"}})

    with pytest.raises(BadRequestError) as caught:
        resolve_project_ai_config(Settings(), revision)

    assert caught.value.code == "generation_model_not_allowed"


def test_custom_execution_materializes_without_return_count_alias() -> None:
    execution = {
        **execution_values(RAG_EXECUTION_PROFILES["standard"]),
        "profile_id": "custom",
        "retrieval_top_k": 7,
    }
    resolution = resolve_project_ai_config(
        Settings(),
        _revision({"execution": execution}),
    )

    assert resolution.configuration.retrieval.top_k == 7
    assert resolution.configuration.retrieval.rerank_candidate_window == 25
    assert not hasattr(resolution.configuration.retrieval, "rerank_return_count")


def test_rerank_mode_is_canonical_in_resolver_and_settings_overlay() -> None:
    resolution = resolve_project_ai_config(
        Settings(),
        _revision(
            {
                "execution": {
                    **execution_values(RAG_EXECUTION_PROFILES["standard"]),
                    "profile_id": "custom",
                    "rerank_mode": "cross_language",
                }
            }
        ),
    )
    effective = apply_effective_ai_config(Settings(), resolution)

    assert resolution.configuration.retrieval.rerank_mode.value == "cross_language"
    assert effective.retrieval.rerank_mode.value == "cross_language"
    assert not hasattr(effective.retrieval, "rerank_enabled")


def test_deprecated_request_overrides_are_always_rejected() -> None:
    with pytest.raises(BadRequestError) as caught:
        resolve_project_ai_config(
            Settings(),
            None,
            deprecated_overrides={"temperature": None},
        )

    assert caught.value.code == "request_policy_override_forbidden"


def test_job_snapshot_captures_v2_provenance_and_no_secrets() -> None:
    settings = Settings(llm={"openai_api_key": "never-store", "model": "global"})
    revision = _revision({"behavior": {"domain_instructions": "Acme support."}})
    resolution = resolve_project_ai_config(settings, revision)

    snapshot = build_job_configuration(
        settings,
        resolution=resolution,
        active_index_build_id=str(uuid.uuid4()),
        source_metadata_generation=8,
    )

    assert snapshot.schema_version == 5
    assert snapshot.provenance["project_config_revision_id"] == str(revision.id)
    assert snapshot.provenance["source_metadata_generation"] == 8
    assert "never-store" not in str(snapshot.model_dump())


def test_effective_snapshot_preserves_phase1_grounding_defaults() -> None:
    settings = Settings(chat={"grounding_mode": "strict"})
    resolution = resolve_project_ai_config(settings, None)
    effective = apply_effective_ai_config(settings, resolution)

    assert effective.chat.grounding_mode.value == "strict"
    assert effective.chat.high_confidence_band_enabled is False
    assert not hasattr(effective.chat, "candidate_wise_grounding_enabled")
