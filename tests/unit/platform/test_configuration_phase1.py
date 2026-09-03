"""Phase-1 V2-only configuration and artifact regression gates."""

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
from app.platform.config.legacy_reset import (
    legacy_project_configuration_to_v2,
    reset_snapshot_configuration,
)
from app.platform.config.project_ai import (
    ConfigProvenance,
    ConfigRevisionRecord,
    EffectiveProjectAIConfig,
    ProjectAIConfig,
    config_revision_record,
    resolve_project_ai_config,
    stable_hash,
)
from app.platform.jobs.contracts import JobConfiguration

pytestmark = pytest.mark.unit


def _revision(payload: dict[str, object]) -> ConfigRevisionRecord:
    return ConfigRevisionRecord(
        id=uuid.uuid4(),
        revision_number=7,
        configuration_hash=stable_hash(payload),
        configuration=payload,
        schema_version=2,
    )


def test_catalog_covers_v2_effective_request_and_snapshot_surfaces() -> None:
    surfaces = (
        (Settings, "settings"),
        (ProjectAIConfig, "project.v2"),
        (EffectiveProjectAIConfig, "snapshot.effective"),
        (JobConfiguration, "snapshot.job"),
        (SearchRequest, "request.search"),
    )
    catalog = build_catalog(*surfaces)
    expected = set().union(*(model_leaf_paths(model, prefix) for model, prefix in surfaces))

    assert expected <= catalog.keys()
    assert all(
        entry.owner and entry.lifecycle and entry.effect_timing for entry in catalog.values()
    )


def test_v2_surface_rejects_retired_provider_safety_and_alias_controls() -> None:
    for payload in (
        {"llm": {"provider": "openai", "model": "arbitrary"}},
        {"retrieval": {"rerank_enabled": False}},
        {"execution": {"rerank_return_count": 5}},
        {"chat": {"include_citations": False}},
        {"source_policy_mode": "off"},
    ):
        with pytest.raises(ValidationError):
            ProjectAIConfig.model_validate(payload)


def test_resolver_rejects_historical_v1_instead_of_runtime_compatibility() -> None:
    payload = {"retrieval": {"rerank_enabled": False}}
    revision = ConfigRevisionRecord.model_construct(
        id=uuid.uuid4(),
        revision_number=1,
        configuration_hash=stable_hash(payload),
        configuration=payload,
        schema_version=1,
        created_at=None,
    )

    with pytest.raises(BadRequestError) as caught:
        resolve_project_ai_config(Settings(), revision)

    assert caught.value.code == "legacy_project_config_requires_reset"


def test_one_shot_reset_maps_active_v1_to_complete_custom_v2() -> None:
    reset = legacy_project_configuration_to_v2(
        Settings(),
        {
            "retrieval": {
                "top_k": 11,
                "rerank_enabled": False,
                "rerank_top_n": 40,
                "rerank_return_n": 7,
                "modifies_expansion_enabled": False,
            },
            "chat": {"response_mode": "indexed_only"},
        },
    )
    payload = reset.model_dump(mode="json", exclude_none=True)

    assert payload["execution"]["profile_id"] == "custom"
    assert payload["execution"]["retrieval_top_k"] == 11
    assert payload["execution"]["rerank_candidate_window"] == 40
    assert payload["execution"]["rerank_mode"] == "always"
    assert "rerank_return_count" not in str(payload)
    assert "modifies_expansion_enabled" not in str(payload)


def test_snapshot_reset_backfills_canonical_fields_and_strips_retired_aliases() -> None:
    reset = reset_snapshot_configuration(
        {
            "retrieval": {
                "rerank_enabled": False,
                "rerank_top_n": 17,
                "modifies_expansion_enabled": True,
                "auto_embed": True,
            }
        }
    )

    assert reset["retrieval"]["rerank_mode"] == "off"
    assert reset["retrieval"]["rerank_candidate_window"] == 17
    assert reset["retrieval"]["modifies_expansion_mode"] == "expand"
    assert "rerank_enabled" not in reset["retrieval"]
    assert "auto_embed" not in reset["retrieval"]


def test_snapshot_reset_strips_retired_chat_grounding_keys() -> None:
    from app.core.config import ChatConfig

    reset = reset_snapshot_configuration(
        {
            "chat": {
                "candidate_wise_grounding_enabled": True,
                "evidence_score_mode": "passage_max",
                "grounding_mode": "strict",
            },
            "invariants": {"candidate_wise_grounding_invariant": False},
        }
    )

    assert "candidate_wise_grounding_enabled" not in reset["chat"]
    assert "evidence_score_mode" not in reset["chat"]
    assert "candidate_wise_grounding_invariant" not in reset["invariants"]
    ChatConfig.model_validate(reset["chat"])


def test_v2_invariants_preserve_current_grounding_semantics() -> None:
    settings = Settings(
        retrieval={"strategy": "semantic", "rerank_mode": "off"},
        chat={"grounding_mode": "strict"},
    )

    resolution = resolve_project_ai_config(settings, None)

    assert resolution.configuration.retrieval.strategy == "hybrid"
    assert resolution.configuration.retrieval.rerank_mode == "always"
    assert resolution.configuration.chat.grounding_mode == "strict"
    assert resolution.configuration.chat.high_confidence_band_enabled is False
    assert "candidate_wise_grounding_invariant" not in resolution.invariants.model_dump()


def test_index_artifact_fingerprint_changes_only_for_materialized_behavior() -> None:
    baseline = build_index_artifact_config(Settings())
    query_tuned = build_index_artifact_config(
        Settings(retrieval={"default_top_k": 31, "rerank_candidate_window": 40})
    )
    artifact_changed = build_index_artifact_config(Settings(chunking={"target_tokens": 300}))

    assert baseline == query_tuned
    assert required_index_action(baseline, query_tuned) is RequiredIndexAction.NONE
    assert required_index_action(baseline, artifact_changed) is RequiredIndexAction.REPROCESS


def test_retired_environment_inputs_have_no_runtime_surface() -> None:
    settings = Settings(retrieval={"auto_embed": True, "rerank_return_count": 4})

    assert not hasattr(settings.retrieval, "auto_embed")
    assert not hasattr(settings.retrieval, "rerank_return_count")


def test_retired_chat_grounding_inputs_are_ignored() -> None:
    with pytest.warns(UserWarning, match="candidate_wise_grounding_enabled"):
        settings = Settings(chat={"candidate_wise_grounding_enabled": True})
    assert not hasattr(settings.chat, "candidate_wise_grounding_enabled")
    with pytest.warns(UserWarning, match="evidence_score_mode"):
        Settings(chat={"evidence_score_mode": "whole_chunk"})
    with pytest.warns(UserWarning, match="REQUEST_OVERRIDE_MODE"):
        Settings(ai_policy={"request_override_mode": "strict"})


def test_config_revision_record_rejects_historical_v1() -> None:
    payload = {"retrieval": {"rerank_mode": "always"}}
    revision = ConfigRevisionRecord.model_construct(
        id=uuid.uuid4(),
        revision_number=1,
        configuration_hash=stable_hash(payload),
        configuration=payload,
        schema_version=1,
    )

    with pytest.raises(BadRequestError) as caught:
        config_revision_record(revision)

    assert caught.value.code == "legacy_project_config_requires_reset"


def test_effective_hash_is_separate_from_resolution_fingerprint() -> None:
    first = resolve_project_ai_config(Settings(), None)
    second = resolve_project_ai_config(Settings(), _revision({}))

    assert first.effective_value_hash == second.effective_value_hash
    assert first.resolution_fingerprint != second.resolution_fingerprint
    assert first.secret_free_snapshot()["schema_version"] == 4


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
