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
    assert resolution.configuration.chat.response_mode == "indexed_only"


def test_project_can_override_response_mode_when_web_provider_is_configured() -> None:
    settings = Settings(
        web_search={"backend": "openai", "openai_api_key": "test-key"},
    )
    revision = _revision({"chat": {"response_mode": "indexed_then_web"}})

    resolution = resolve_project_ai_config(settings, revision)

    assert resolution.configuration.chat.response_mode == "indexed_then_web"
    assert resolution.origins["chat.response_mode"] == "project"
    assert resolution.secret_free_snapshot()["schema_version"] == 2


def test_web_search_inherits_openai_llm_settings_when_unspecified() -> None:
    settings = Settings(
        llm={
            "backend": "openai",
            "model": "shared-model",
            "openai_api_key": "test-key",
            "openai_base_url": "https://gateway.example/v1",
        },
    )
    revision = _revision({"chat": {"response_mode": "indexed_then_web"}})

    resolution = resolve_project_ai_config(settings, revision)
    effective = apply_effective_ai_config(settings, resolution)

    assert effective.resolved_web_search_backend().value == "openai"
    assert effective.resolved_web_search_model() == "shared-model"
    assert effective.resolved_web_search_api_key() == "test-key"
    assert effective.resolved_web_search_base_url() == "https://gateway.example/v1"
    assert resolution.origins["web_search.model"] == "global_llm"
    assert resolution.configuration.web_search.max_results == 8
    assert resolution.configuration.web_search.max_evidence_chars == 12_000


def test_project_can_disable_or_bound_web_search() -> None:
    settings = Settings(
        llm={"backend": "openai", "model": "shared-model", "openai_api_key": "test-key"},
    )
    disabled = _revision(
        {
            "chat": {"response_mode": "indexed_then_web"},
            "web_search": {"enabled": False},
        }
    )
    with pytest.raises(BadRequestError) as exc_info:
        resolve_project_ai_config(settings, disabled)
    assert exc_info.value.code == "web_search_disabled_for_project"

    revision = _revision(
        {
            "chat": {"response_mode": "indexed_and_web"},
            "web_search": {
                "enabled": True,
                "model": "project-search-model",
                "max_results": 4,
                "max_evidence_chars": 4000,
                "max_output_tokens": 800,
                "request_timeout_seconds": 20,
            },
        }
    )
    resolution = resolve_project_ai_config(settings, revision)
    effective = apply_effective_ai_config(settings, resolution)

    assert resolution.configuration.web_search.model == "project-search-model"
    assert resolution.origins["web_search.max_results"] == "project"
    assert effective.web_search.max_results == 4
    assert effective.web_search.max_evidence_chars == 4000
    assert effective.web_search.max_output_tokens == 800
    assert effective.web_search.request_timeout_seconds == 20


def test_modifies_expansion_is_default_off_and_project_bounded() -> None:
    settings = Settings()
    default_resolution = resolve_project_ai_config(settings, None)

    assert default_resolution.configuration.retrieval.modifies_expansion_enabled is False
    assert default_resolution.configuration.retrieval.max_related_sources == 8
    assert default_resolution.configuration.retrieval.max_relationship_candidates == 20

    revision = _revision(
        {
            "retrieval": {
                "modifies_expansion_enabled": True,
                "max_related_sources": 4,
                "max_relationship_candidates": 12,
            }
        }
    )
    resolution = resolve_project_ai_config(settings, revision)

    assert resolution.configuration.retrieval.modifies_expansion_enabled is True
    assert resolution.configuration.retrieval.max_related_sources == 4
    assert resolution.configuration.retrieval.max_relationship_candidates == 12


def test_web_response_mode_rejects_missing_provider() -> None:
    revision = _revision({"chat": {"response_mode": "indexed_and_web"}})

    with pytest.raises(BadRequestError) as exc_info:
        resolve_project_ai_config(Settings(), revision)

    assert exc_info.value.code == "web_search_not_configured"


def test_chat_runtime_can_resolve_web_policy_when_provider_is_temporarily_unavailable() -> None:
    revision = _revision({"chat": {"response_mode": "indexed_and_web"}})

    resolution = resolve_project_ai_config(
        Settings(),
        revision,
        validate_web_provider=False,
    )

    assert resolution.configuration.chat.response_mode == "indexed_and_web"


def test_web_response_mode_rejects_legacy_prompt_compatibility_override() -> None:
    settings = Settings(
        web_search={"backend": "openai", "openai_api_key": "test-key"},
    )
    revision = _revision({"chat": {"response_mode": "indexed_then_web"}})

    with pytest.raises(BadRequestError) as exc_info:
        resolve_project_ai_config(
            settings,
            revision,
            deprecated_overrides={"prompt_version": "v4"},
        )

    assert exc_info.value.code == "web_response_mode_requires_source_prompt"


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


def test_legacy_rrf_scale_evidence_threshold_is_ignored() -> None:
    revision = _revision({"retrieval": {"evidence_score_threshold": 0.01}})

    resolution = resolve_project_ai_config(Settings(), revision)

    assert resolution.configuration.retrieval.semantic_evidence_score_threshold == 0.35
    assert resolution.origins["retrieval.evidence_score_threshold"] == "global"


def test_legacy_query_coverage_override_is_ignored() -> None:
    revision = _revision({"chat": {"minimum_query_token_coverage": 0.15}})

    resolution = resolve_project_ai_config(Settings(), revision)

    assert resolution.configuration.chat.lexical_corroboration_coverage == 0.50
    assert resolution.origins["chat.minimum_query_token_coverage"] == "global"


def test_project_can_raise_semantic_threshold_and_set_rescue_floor() -> None:
    revision = _revision(
        {
            "retrieval": {"evidence_score_threshold": 0.5},
            "chat": {"lexical_corroboration_floor_score": 0.3},
        }
    )
    resolution = resolve_project_ai_config(Settings(), revision)
    restored = apply_effective_ai_config(Settings(), resolution)

    assert resolution.configuration.retrieval.semantic_evidence_score_threshold == 0.5
    assert resolution.configuration.chat.lexical_corroboration_floor_score == 0.3
    assert restored.chat.minimum_semantic_evidence_score == 0.5
    assert restored.chat.lexical_corroboration_floor_score == 0.3


def test_rescue_floor_above_semantic_bar_is_rejected() -> None:
    revision = _revision({"chat": {"lexical_corroboration_floor_score": 0.5}})

    with pytest.raises(BadRequestError) as caught:
        resolve_project_ai_config(Settings(), revision)

    assert caught.value.code == "invalid_evidence_thresholds"


def test_effective_policy_applies_the_lexical_rescue_floor() -> None:
    settings = Settings()
    revision = _revision({"retrieval": {"evidence_score_threshold": 0.5}})
    resolution = resolve_project_ai_config(settings, revision)
    restored = apply_effective_ai_config(settings, resolution)

    assert resolution.configuration.retrieval.semantic_evidence_score_threshold == 0.5
    assert resolution.configuration.chat.lexical_corroboration_floor_score == 0.30
    assert restored.chat.minimum_semantic_evidence_score == 0.5
    assert restored.chat.lexical_corroboration_floor_score == 0.30


def test_passage_evidence_mode_is_versioned_in_effective_snapshot() -> None:
    revision = _revision(
        {
            "retrieval": {
                "passage_scoring_enabled": True,
                "passage_window_tokens": 80,
                "passage_overlap_tokens": 20,
                "passage_min_tokens": 30,
            },
            "chat": {"evidence_score_mode": "passage_max"},
        }
    )

    resolution = resolve_project_ai_config(Settings(), revision)
    restored = apply_effective_ai_config(Settings(), resolution)

    assert resolution.configuration.chat.evidence_score_mode == "passage_max"
    assert resolution.configuration.retrieval.passage_window_tokens == 80
    assert restored.chat.evidence_score_mode == "passage_max"
    assert restored.retrieval.passage_scoring_enabled is True
    assert (
        resolution.secret_free_snapshot()["configuration"]["chat"]["evidence_score_mode"]
        == "passage_max"
    )


def test_evidence_gate_mode_is_inherited_into_chat_config() -> None:
    revision = _revision({"chat": {"evidence_gate_mode": "observe"}})

    resolution = resolve_project_ai_config(Settings(), revision)
    restored = apply_effective_ai_config(Settings(), resolution)

    assert resolution.configuration.chat.evidence_gate_mode.value == "observe"
    assert restored.chat.evidence_gate_mode.value == "observe"
    assert Settings().chat.evidence_gate_mode.value == "enforce"


def test_candidate_wise_grounding_can_be_enabled_for_a_project_canary() -> None:
    revision = _revision({"chat": {"candidate_wise_grounding_enabled": True}})

    resolution = resolve_project_ai_config(Settings(), revision)
    restored = apply_effective_ai_config(Settings(), resolution)

    assert resolution.configuration.chat.candidate_wise_grounding_enabled is True
    assert resolution.origins["chat.candidate_wise_grounding_enabled"] == "project"
    assert restored.chat.candidate_wise_grounding_enabled is True


def test_passage_evidence_mode_requires_passage_scoring() -> None:
    revision = _revision({"chat": {"evidence_score_mode": "passage_max"}})

    with pytest.raises(BadRequestError) as caught:
        resolve_project_ai_config(Settings(), revision)

    assert caught.value.code == "passage_evidence_scoring_disabled"


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


def test_rerank_mode_inherits_and_maps_legacy_enabled() -> None:
    inherited = resolve_project_ai_config(Settings(), None)
    assert inherited.configuration.retrieval.rerank_mode.value == "always"
    assert inherited.configuration.retrieval.rerank_enabled is True
    assert inherited.origins["retrieval.rerank_mode"] == "global"

    always = resolve_project_ai_config(
        Settings(),
        _revision({"retrieval": {"rerank_mode": "cross_language"}}),
    )
    assert always.configuration.retrieval.rerank_mode.value == "cross_language"
    assert always.configuration.retrieval.rerank_enabled is True
    assert always.origins["retrieval.rerank_mode"] == "project"

    legacy_off = resolve_project_ai_config(
        Settings(),
        _revision({"retrieval": {"rerank_enabled": False}}),
    )
    assert legacy_off.configuration.retrieval.rerank_mode.value == "off"
    assert legacy_off.configuration.retrieval.rerank_enabled is False

    mode_wins = resolve_project_ai_config(
        Settings(),
        _revision({"retrieval": {"rerank_mode": "always", "rerank_enabled": False}}),
    )
    assert mode_wins.configuration.retrieval.rerank_mode.value == "always"
    assert mode_wins.configuration.retrieval.rerank_enabled is True


def test_apply_effective_ai_config_overlays_rerank_mode() -> None:
    settings = Settings()
    resolution = resolve_project_ai_config(
        settings,
        _revision({"retrieval": {"rerank_mode": "off"}}),
    )
    overlay = apply_effective_ai_config(settings, resolution)
    assert overlay.retrieval.rerank_mode.value == "off"
    assert overlay.retrieval.rerank_enabled is False


def test_index_hash_ignores_query_translation_and_reranker_quality_settings() -> None:
    first = build_job_configuration(Settings(query_translation={"enabled": False}))
    second = build_job_configuration(
        Settings(
            query_translation={"enabled": True, "model": "gpt-5-mini"},
            retrieval={"reranker_backend": "cohere"},
        )
    )

    assert first.index_output_digest() == second.index_output_digest()
    assert first.quality["query_translation"]["enabled"] is False
    assert second.quality["query_translation"]["enabled"] is True
