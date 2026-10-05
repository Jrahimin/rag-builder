"""Quality acceptance must match the current runtime and an immutable corpus."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.chunk_keyword_index import ChunkKeywordIndex
from app.models.document_chunk import DocumentChunk
from app.models.index_build import IndexBuild, ProjectIndexPointer
from app.models.project import Project
from app.models.project_ai_config_revision import ProjectAIConfigRevision
from app.modules.retrieval.repositories.index_acceptance_repository import IndexAcceptanceRepository
from app.platform.config.project_ai import config_revision_record, resolve_project_ai_config
from app.platform.domain.runtime_identity import runtime_code_fingerprint
from app.platform.jobs.configuration import build_job_configuration
from app.platform.jobs.errors import PermanentJobError


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


class AcceptanceCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1, max_length=160)
    repetition: int = Field(ge=1, le=3)
    expected: Literal["answered", "partial", "insufficient_evidence", "unresolved_authority"]
    actual: Literal[
        "answered",
        "partial",
        "insufficient_evidence",
        "unresolved_authority",
        "verification_failed",
        "timed_out",
    ]
    evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    assertions_verified: bool
    scope_verified: bool


class AcceptanceSetDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["acceptance.set.v1"] = "acceptance.set.v1"
    project_id: uuid.UUID
    build_id: uuid.UUID
    revision: str = Field(min_length=1, max_length=160)
    certification: Literal["offline_fixture", "production"]
    case_expectations: dict[
        str, Literal["answered", "partial", "insufficient_evidence", "unresolved_authority"]
    ] = Field(min_length=1, max_length=1000)
    repetitions: int = Field(ge=1, le=3)
    require_comparison: bool = True


REMEDIATION_PROJECT_ID = uuid.UUID("2ee2756f-ad27-44df-a9d3-1316b10ccbb1")
REMEDIATION_DOCUMENT_ID = "300fdea8-48a7-43aa-96a5-d3a020e2a2ad"
REMEDIATION_CASE_IDS = {
    "Q1",
    "Q2",
    "Q3",
    "Q4",
    "Q5",
    "Q6",
    "Q7",
    "earlier_agm",
    "earlier_threshold",
    "dncc",
    "rent",
    "partnership",
}


def validate_definition(definition: AcceptanceSetDefinition, build: IndexBuild) -> None:
    if definition.project_id != build.project_id or definition.build_id != build.id:
        raise PermanentJobError(
            "Acceptance definition belongs to another project/build.",
            code="index_acceptance_set_stale",
        )
    if definition.certification == "production":
        if definition.repetitions != 3 or not definition.require_comparison:
            raise PermanentJobError(
                "Production sets require repeated comparison evidence.",
                code="index_acceptance_set_incomplete",
            )
        remediation = build.project_id == REMEDIATION_PROJECT_ID or any(
            str(row.get("document_id")) == REMEDIATION_DOCUMENT_ID
            for row in (build.manifest or {}).get("documents", [])
        )
        if remediation and (
            definition.revision != "message-journey.v1"
            or set(definition.case_expectations) != REMEDIATION_CASE_IDS
        ):
            raise PermanentJobError(
                "Captured remediation corpus requires its approved benchmark.",
                code="index_acceptance_set_incomplete",
            )


class BuildAcceptanceArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["build.acceptance.v1"] = "build.acceptance.v1"
    certification: Literal["offline_fixture", "production"]
    project_id: uuid.UUID
    build_id: uuid.UUID
    code_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    configuration_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    project_config_revision_id: uuid.UUID | None
    index_configuration_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_generation: int = Field(ge=0)
    corpus_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    build_manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    semantic_structure_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    embedding_identity: dict[str, Any]
    acceptance_set_revision: str = Field(min_length=1, max_length=160)
    acceptance_set_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    report_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    cases: list[AcceptanceCase] = Field(min_length=1, max_length=3000)
    observed_report_id: uuid.UUID | None = None
    observed_report_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    compared_active_build_id: uuid.UUID | None = None
    comparison_report_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class BuildAcceptanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    build_id: uuid.UUID
    artifact_hash: str
    artifact: BuildAcceptanceArtifact
    created_by: str
    created_at: datetime


def embedding_identity(build: IndexBuild) -> dict[str, Any]:
    return {
        key: (build.manifest or {}).get(key)
        for key in (
            "embedding_provider",
            "embedding_model",
            "embedding_dimensions",
            "embedding_set_version",
        )
    }


def validate_acceptance(
    artifact: BuildAcceptanceArtifact,
    build: IndexBuild,
    *,
    project_id: uuid.UUID,
    code: str,
    configuration_hash: str,
    source_generation: int,
    config_revision_id: uuid.UUID | None,
    effective_llm: str,
    active_build_id: uuid.UUID | None = None,
    structure_hash: str | None = None,
    acceptance_definition: AcceptanceSetDefinition | None = None,
    conversation_configuration_hash: str | None = None,
    observed_report: dict[str, Any] | None = None,
    rollback: bool = False,
) -> None:
    expected = {
        "project_id": project_id,
        "build_id": build.id,
        "code_fingerprint": code,
        "configuration_hash": configuration_hash,
        "index_configuration_hash": build.configuration_hash,
        "source_generation": source_generation,
        "project_config_revision_id": config_revision_id,
        "corpus_fingerprint": build.corpus_fingerprint,
        "build_manifest_hash": digest(build.manifest),
        "semantic_structure_hash": structure_hash,
        "embedding_identity": embedding_identity(build),
    }
    mismatch = [key for key, value in expected.items() if getattr(artifact, key) != value]
    cases = [case.model_dump(mode="json") for case in artifact.cases]
    if build.project_id != project_id or mismatch:
        raise PermanentJobError(
            "Quality attestation does not match current identities.",
            code="index_acceptance_stale",
            context={"mismatched_fields": mismatch},
        )
    if artifact.report_hash != digest(cases) or any(
        c.expected != c.actual or not c.assertions_verified or not c.scope_verified
        for c in artifact.cases
    ):
        raise PermanentJobError(
            "Acceptance report contains failed/unverified cases.", code="index_acceptance_failed"
        )
    ids = [(c.case_id, c.repetition) for c in artifact.cases]
    if len(set(ids)) != len(ids) or any(
        value is None for value in artifact.embedding_identity.values()
    ):
        raise PermanentJobError(
            "Acceptance identity or cases are incomplete.", code="index_acceptance_incomplete"
        )
    fixture_settings = get_settings()
    isolated_concept_fixture = (
        artifact.embedding_identity["embedding_provider"] == "fixture-concept"
        and fixture_settings.app.env == "testing"
        and fixture_settings.database.name == fixture_settings.test_database.name == "ape_test"
        and fixture_settings.embedding.backend.value == "hash"
        and fixture_settings.llm.backend.value == "echo"
    )
    if artifact.certification == "offline_fixture" and (
        (
            artifact.embedding_identity["embedding_provider"] != "hash"
            and not isolated_concept_fixture
        )
        or effective_llm != "echo"
    ):
        raise PermanentJobError(
            "Offline fixtures cannot certify a paid/live build.",
            code="index_acceptance_offline_only",
        )
    if acceptance_definition is None:
        raise PermanentJobError(
            "An approved project/build acceptance set is required.",
            code="index_acceptance_set_missing",
        )
    validate_definition(acceptance_definition, build)
    expected_cases = {
        (case_id, repetition)
        for case_id in acceptance_definition.case_expectations
        for repetition in range(1, acceptance_definition.repetitions + 1)
    }
    if (
        artifact.acceptance_set_hash != digest(acceptance_definition.model_dump(mode="json"))
        or artifact.acceptance_set_revision != acceptance_definition.revision
        or artifact.certification != acceptance_definition.certification
        or set(ids) != expected_cases
        or any(
            c.expected != acceptance_definition.case_expectations.get(c.case_id)
            for c in artifact.cases
        )
        or (
            acceptance_definition.require_comparison
            and (
                (not rollback and artifact.compared_active_build_id != active_build_id)
                or not artifact.comparison_report_hash
            )
        )
    ):
        raise PermanentJobError(
            "Receipt does not satisfy the approved project/build set.",
            code="index_acceptance_incomplete",
        )

    if artifact.certification == "production":
        report_identity = (observed_report or {}).get("identity") or {}
        report_baseline = report_identity.get("active_build_id")
        first_build = (observed_report or {}).get("comparison_mode") == "first_build"
        artifact_baseline = (
            str(artifact.compared_active_build_id) if artifact.compared_active_build_id else None
        )
        if (
            artifact.observed_report_id is None
            or observed_report is None
            or artifact.observed_report_hash != digest(observed_report)
            or observed_report.get("cases") != cases
            or observed_report.get("comparison_hash") != artifact.comparison_report_hash
            or report_identity.get("code") != code
            or report_identity.get("configuration_hash") != configuration_hash
            or report_identity.get("conversation_configuration_hash")
            != conversation_configuration_hash
            or report_identity.get("source_generation") != source_generation
            or report_identity.get("project_id") != str(project_id)
            or report_baseline != artifact_baseline
            or (
                not rollback
                and report_baseline != (str(active_build_id) if active_build_id else None)
            )
            or (first_build and report_baseline is not None)
            or (not first_build and report_baseline is None)
            or (observed_report.get("structures") or {}).get(str(build.id)) != structure_hash
        ):
            raise PermanentJobError(
                "Production receipt lacks matching observed Message evidence.",
                code="index_acceptance_observed_missing",
            )


async def current_acceptance_identity(
    session: AsyncSession, project_id: uuid.UUID
) -> dict[str, Any]:
    project = await session.get(Project, project_id, with_for_update=True)
    if project is None:
        raise PermanentJobError(
            "Acceptance Project does not exist.", code="index_acceptance_project_missing"
        )
    revision = (
        await session.get(ProjectAIConfigRevision, project.active_ai_config_revision_id)
        if project.active_ai_config_revision_id
        else None
    )
    pointer = await session.get(ProjectIndexPointer, project_id)
    settings = get_settings()
    resolution = resolve_project_ai_config(settings, config_revision_record(revision))
    configuration = build_job_configuration(settings, resolution=resolution)
    return {
        "project_id": project_id,
        "code": runtime_code_fingerprint(),
        "configuration_hash": configuration.output_digest(),
        "conversation_configuration_hash": resolution.configuration_hash,
        "source_generation": project.source_metadata_generation,
        "config_revision_id": project.active_ai_config_revision_id,
        "effective_llm": configuration.quality["llm"]["backend"],
        "active_build_id": pointer.active_build_id if pointer else None,
    }


async def semantic_snapshot_hash(
    session: AsyncSession, project_id: uuid.UUID, build: IndexBuild
) -> str:
    rows = (
        await session.execute(
            select(DocumentChunk, ChunkKeywordIndex.metadata_snapshot)
            .join(
                ChunkKeywordIndex,
                (ChunkKeywordIndex.chunk_id == DocumentChunk.id)
                & (ChunkKeywordIndex.project_id == DocumentChunk.project_id),
            )
            .where(
                ChunkKeywordIndex.project_id == project_id,
                ChunkKeywordIndex.index_build_id == build.id,
            )
            .order_by(DocumentChunk.id)
        )
    ).all()
    if len(rows) != build.chunk_count:
        raise PermanentJobError(
            "Semantic snapshot membership differs from the sealed build.",
            code="index_acceptance_stale",
        )
    from app.models.index_scope_review import IndexScopeReview

    reviews = (
        await session.scalars(
            select(IndexScopeReview)
            .where(IndexScopeReview.project_id == project_id, IndexScopeReview.build_id == build.id)
            .order_by(IndexScopeReview.chunk_id)
        )
    ).all()
    return digest(
        {
            "reviews": [
                {"chunk_id": str(v.chunk_id), "review_hash": v.review_hash, "envelope": v.envelope}
                for v in reviews
            ],
            "chunks": [
                {
                    "chunk_id": str(chunk.id),
                    "document_id": str(chunk.document_id),
                    "version": chunk.document_version,
                    "generation": str(chunk.generation_id) if chunk.generation_id else None,
                    "content_hash": hashlib.sha256(chunk.content.encode()).hexdigest(),
                    "scope": chunk.chunk_metadata,
                    "indexed_metadata": indexed_metadata,
                }
                for chunk, indexed_metadata in rows
            ],
        }
    )


async def require_build_acceptance(
    session: AsyncSession, project_id: uuid.UUID, build: IndexBuild, *, rollback: bool = False
) -> None:
    """Called under the activation lock before any pointer or readiness mutation."""
    repository = IndexAcceptanceRepository(session, project_id)
    rows = await repository.list(build.id)
    identity = await current_acceptance_identity(session, project_id)
    if rollback:
        from app.models.index_build import IndexBuildState

        pointer = await session.get(ProjectIndexPointer, project_id, with_for_update=True)
        if (
            build.state != IndexBuildState.RETAINED
            or pointer is None
            or pointer.previous_build_id != build.id
            or pointer.active_build_id is None
        ):
            raise PermanentJobError(
                "Rollback must target the exact retained previous build.",
                code="index_acceptance_stale",
            )
    # Missing acceptance returns before touching the pointer or computing a candidate receipt.
    if not rows:
        raise PermanentJobError(
            "Quality acceptance is required before activation.", code="index_acceptance_missing"
        )
    definition = await repository.definition(build.id)
    if definition is None or definition.definition_hash != digest(definition.definition):
        raise PermanentJobError(
            "Approved acceptance definition is missing or invalid.",
            code="index_acceptance_set_missing",
        )
    identity["acceptance_definition"] = AcceptanceSetDefinition.model_validate(
        definition.definition
    )
    identity["structure_hash"] = await semantic_snapshot_hash(session, project_id, build)
    errors: list[PermanentJobError] = []
    for row in rows:
        try:
            artifact = BuildAcceptanceArtifact.model_validate(row.artifact)
            if row.artifact_hash != digest(row.artifact):
                raise PermanentJobError(
                    "Acceptance payload hash mismatch.", code="index_acceptance_stale"
                )
            report = (
                await repository.observed_report(artifact.observed_report_id, build.id)
                if artifact.certification == "production"
                else None
            )
            validate_acceptance(
                artifact, build, **identity, observed_report=report, rollback=rollback
            )
            return
        except ValueError as exc:
            errors.append(PermanentJobError(str(exc), code="index_acceptance_incomplete"))
        except PermanentJobError as exc:
            errors.append(exc)
    if errors:
        raise errors[0]
    raise PermanentJobError(
        "Integrity validation requires separate quality acceptance before activation.",
        code="index_acceptance_missing",
    )
