"""Phase 1 Project policy revision and ownership-lock tests."""

from __future__ import annotations

import uuid
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.platform.config.profiles as profile_registry
from app.core.config import Settings
from app.core.exceptions import ConflictError
from app.models.project import Project
from app.models.project_ai_config_revision import ProjectAIConfigRevision
from app.modules.projects.services.project_ai_config_service import (
    ProjectAdministrationService,
)
from app.platform.config.profiles import (
    PROFILE_CERTIFICATIONS,
    RAG_EXECUTION_PROFILES,
    CertificationStatus,
    ProfileCertification,
    execution_values,
)
from app.platform.config.project_ai import (
    ConfigRevisionRecord,
    ProjectAIConfig,
    materialize_execution_values,
    resolve_project_ai_config,
)
from app.platform.domain.auth_context import DEFAULT_ORGANIZATION_ID

pytestmark = pytest.mark.unit


def _project(*, locked: bool, organization_id: uuid.UUID = DEFAULT_ORGANIZATION_ID) -> Project:
    return Project(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name="Legacy",
        description=None,
        is_active=True,
        ownership_locked=locked,
        active_ai_config_revision_id=None,
        source_metadata_generation=0,
        deleted_at=None,
        deleted_by=None,
    )


def _service(
    session: AsyncMock,
    repository: AsyncMock,
    audit: MagicMock,
    project_id: uuid.UUID,
    settings: Settings | None = None,
) -> ProjectAdministrationService:
    return ProjectAdministrationService(
        session=session,
        project_id=project_id,
        repository=repository,
        settings=settings or Settings(),
        audit=audit,
        actor_id="operator-1",
    )


async def test_config_revision_is_append_only_and_activates_pointer() -> None:
    session = AsyncMock()
    repository = AsyncMock()
    repository.add = MagicMock()
    repository.next_revision_number.return_value = 1
    project = _project(locked=True)
    repository.lock_project.return_value = project
    audit = MagicMock()
    service = _service(session, repository, audit, project.id)

    revision = await service.create_revision(
        ProjectAIConfig.model_validate(
            {"behavior": {"domain_instructions": "Answer according to policy."}}
        ),
        expected_active_revision_id=None,
        reason="Initial policy",
    )

    assert revision.revision_number == 1
    assert revision.schema_version == 2
    assert revision.configuration["behavior"]["domain_instructions"] == (
        "Answer according to policy."
    )
    assert project.active_ai_config_revision_id == revision.id
    repository.add.assert_called_once_with(revision)
    session.commit.assert_awaited_once()
    audit.record.assert_called_once()


async def test_preset_write_stores_only_selection_and_clears_custom_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    certifications = {
        profile_id: ProfileCertification(
            profile_id=profile_id,
            status=(
                CertificationStatus.CERTIFIED if profile_id == "standard" else certification.status
            ),
        )
        for profile_id, certification in PROFILE_CERTIFICATIONS.items()
    }
    monkeypatch.setattr(
        profile_registry,
        "PROFILE_CERTIFICATIONS",
        MappingProxyType(certifications),
    )
    session = AsyncMock()
    repository = AsyncMock()
    repository.add = MagicMock()
    repository.next_revision_number.return_value = 1
    project = _project(locked=True)
    repository.lock_project.return_value = project
    service = _service(session, repository, MagicMock(), project.id)

    revision = await service.create_revision(
        ProjectAIConfig.model_validate(
            {
                "execution": {
                    "profile_id": "standard",
                    "retrieval_top_k": 3,
                    "max_context_chunks": 2,
                }
            }
        ),
        expected_active_revision_id=None,
        reason="Select balanced profile",
    )

    assert revision.configuration["execution"] == {"profile_id": "standard"}


async def test_partial_custom_write_materializes_complete_bundle_and_stays_independent() -> None:
    session = AsyncMock()
    repository = AsyncMock()
    repository.add = MagicMock()
    repository.next_revision_number.return_value = 1
    repository.get_active.return_value = None
    project = _project(locked=True)
    repository.lock_project.return_value = project
    standard_settings = Settings(ai_policy={"default_rag_profile": "standard"})
    service = _service(
        session,
        repository,
        MagicMock(),
        project.id,
        settings=standard_settings,
    )

    revision = await service.create_revision(
        ProjectAIConfig.model_validate(
            {"execution": {"profile_id": "custom", "retrieval_top_k": 7}}
        ),
        expected_active_revision_id=None,
        reason="Tune one execution control",
    )

    expected = {**execution_values(RAG_EXECUTION_PROFILES["standard"]), "retrieval_top_k": 7}
    assert revision.configuration["execution"] == {"profile_id": "custom", **expected}
    record = ConfigRevisionRecord(
        id=revision.id,
        revision_number=revision.revision_number,
        configuration_hash=revision.configuration_hash,
        configuration=revision.configuration,
        schema_version=2,
    )
    after_global_change = resolve_project_ai_config(
        Settings(ai_policy={"default_rag_profile": "quality"}), record
    )
    assert materialize_execution_values(after_global_change.configuration) == expected


async def test_config_revision_uses_optimistic_concurrency() -> None:
    session = AsyncMock()
    repository = AsyncMock()
    active_id = uuid.uuid4()
    project = _project(locked=True)
    project.active_ai_config_revision_id = active_id
    repository.lock_project.return_value = project
    service = _service(session, repository, MagicMock(), project.id)

    with pytest.raises(ConflictError) as caught:
        await service.create_revision(
            ProjectAIConfig(),
            expected_active_revision_id=uuid.uuid4(),
            reason="Stale write",
        )

    assert caught.value.code == "project_config_revision_conflict"
    repository.add.assert_not_called()


async def test_active_v1_normalization_previews_then_appends_v2_only_on_confirmation() -> None:
    session = AsyncMock()
    repository = AsyncMock()
    repository.add = MagicMock()
    repository.next_revision_number.return_value = 4
    project = _project(locked=True)
    active = ProjectAIConfigRevision(
        id=uuid.uuid4(),
        project_id=project.id,
        revision_number=3,
        schema_version=1,
        configuration_hash="a" * 64,
        configuration={
            "retrieval": {"rerank_enabled": False, "rerank_top_n": 30},
            "chat": {"include_citations": False},
        },
        created_by="legacy",
        source="legacy_v1",
        reason="Historical",
    )
    project.active_ai_config_revision_id = active.id
    repository.lock_project.return_value = project
    repository.get_active.return_value = active
    service = _service(session, repository, MagicMock(), project.id)

    source, preview = await service.normalization_preview()

    assert source.id == active.id
    assert preview.configuration.execution.rerank_mode == "always"
    assert preview.required_index_action == "none"
    repository.add.assert_not_called()

    normalized = await service.normalize_active_v1(
        expected_active_revision_id=active.id,
        reason="Remove legacy live semantics",
    )

    assert normalized.schema_version == 2
    assert normalized.source == "super_admin_v1_normalization"
    assert normalized.restored_from_revision_id == active.id
    assert project.active_ai_config_revision_id == normalized.id
    assert active.configuration["retrieval"]["rerank_enabled"] is False
    repository.add.assert_called_once_with(normalized)


async def test_locked_project_cannot_be_reassigned() -> None:
    session = AsyncMock()
    repository = AsyncMock()
    project = _project(locked=True)
    repository.lock_project.return_value = project
    service = _service(session, repository, MagicMock(), project.id)

    with pytest.raises(ConflictError) as caught:
        await service.reassign_ownership(
            expected_current_organization_id=project.organization_id,
            target_organization_id=uuid.uuid4(),
            reason="Move",
        )

    assert caught.value.code == "project_ownership_locked"


async def test_legacy_confirmation_locks_ownership_without_changing_id() -> None:
    session = AsyncMock()
    repository = AsyncMock()
    project = _project(locked=False)
    repository.lock_project.return_value = project
    audit = MagicMock()
    service = _service(session, repository, audit, project.id)

    result = await service.confirm_ownership(
        expected_current_organization_id=DEFAULT_ORGANIZATION_ID,
        reason="Default Organization is correct",
    )

    assert result.id == project.id
    assert result.organization_id == DEFAULT_ORGANIZATION_ID
    assert result.ownership_locked is True
    session.commit.assert_awaited_once()
    detail = audit.record.call_args.kwargs["detail"]
    assert detail["previous_organization_id"] == str(DEFAULT_ORGANIZATION_ID)
    assert detail["target_organization_id"] == str(DEFAULT_ORGANIZATION_ID)
