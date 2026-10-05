"""Observed exact-quote acceptance for disposable hash/echo corpus fixtures only."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.composition.audit import DatabaseAuditRecorder
from app.core.config import ChatConfig, get_settings
from app.models.chunk_keyword_index import ChunkKeywordIndex
from app.models.document_chunk import DocumentChunk
from app.models.index_build import IndexBuild
from app.modules.conversations.grounding_service import GroundingService
from app.modules.conversations.ports import ContextChunk
from app.modules.retrieval.build_acceptance import (
    AcceptanceCase,
    AcceptanceSetDefinition,
    BuildAcceptanceArtifact,
    current_acceptance_identity,
    digest,
    embedding_identity,
    semantic_snapshot_hash,
)
from app.modules.retrieval.services.build_acceptance_service import BuildAcceptanceService
from app.platform.domain.content_hash import content_hash


async def grounded_fixture_proof(chunks):
    """Deterministic documentary proof; headings cannot authorize a quality receipt."""
    ordered = sorted(
        chunks,
        key=lambda chunk: (content_hash(chunk.content), chunk.chunk_index, str(chunk.document_id)),
    )
    for source in ordered:
        factual_body = re.sub(r"(?m)^\s{0,3}#{1,6}\s+.*$", "", source.content).strip()
        if not factual_body:
            continue
        chunk = ContextChunk(
            chunk_id=source.id,
            document_id=source.document_id,
            chunk_index=source.chunk_index,
            content=source.content,
            score=1.0,
            filename="disposable-fixture",
            chunk_hash=content_hash(source.content),
            metadata=source.chunk_metadata,
        )
        grounding = await GroundingService(ChatConfig()).map_claims(
            source.content + " [1]",
            [chunk],
            draft_segments=[
                {
                    "text": source.content + " [1]",
                    "assertion_id": "fixture-exact",
                    "requirement_ids": [],
                    "proof_ids": [str(source.id)],
                }
            ],
        )
        if (
            grounding.grounded is True
            and grounding.claims
            and all(
                c.get("verification") == "supported"
                and c.get("grounded") is True
                and c.get("evidence")
                for c in grounding.claims
            )
        ):
            return grounding
    raise AssertionError("Sealed fixture corpus has no genuinely grounded factual quotation")


async def fixture_artifact(
    session: AsyncSession,
    build: IndexBuild,
    *,
    revision: str = "disposable.exact-quote.v1",
    case_id: str = "disposable-exact-quote-or-empty",
) -> BuildAcceptanceArtifact:
    settings = get_settings()
    assert settings.app.env == "testing"
    assert settings.database.name == settings.test_database.name == "ape_test"
    assert settings.embedding.backend.value == "hash" and settings.llm.backend.value == "echo"
    chunks = (
        (
            await session.execute(
                select(DocumentChunk)
                .join(
                    ChunkKeywordIndex,
                    (ChunkKeywordIndex.chunk_id == DocumentChunk.id)
                    & (ChunkKeywordIndex.project_id == DocumentChunk.project_id),
                )
                .where(
                    ChunkKeywordIndex.project_id == build.project_id,
                    ChunkKeywordIndex.index_build_id == build.id,
                )
                .order_by(DocumentChunk.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(chunks) == build.chunk_count == build.vector_count == build.keyword_count
    identity = await current_acceptance_identity(session, build.project_id)
    actual = "insufficient_evidence"
    if chunks:
        grounding = await grounded_fixture_proof(chunks)
        actual = "answered"
        evidence_hash = digest(grounding.claims)
    else:
        actual = "insufficient_evidence"
        evidence_hash = digest({"observed_keyword_rows": 0, "build_id": str(build.id)})
    case = AcceptanceCase(
        case_id=case_id,
        repetition=1,
        expected=actual,
        actual=actual,
        evidence_hash=evidence_hash,
        assertions_verified=True,
        scope_verified=True,
    )
    definition = AcceptanceSetDefinition(
        project_id=build.project_id,
        build_id=build.id,
        revision=revision,
        certification="offline_fixture",
        repetitions=1,
        require_comparison=False,
        case_expectations={case.case_id: actual},
    )
    await BuildAcceptanceService(session, build.project_id).define(
        build.id,
        definition,
        actor_id="disposable-test-fixture",
        audit=DatabaseAuditRecorder(session, build.project_id),
    )
    cases = [case.model_dump(mode="json")]
    return BuildAcceptanceArtifact(
        certification="offline_fixture",
        project_id=build.project_id,
        build_id=build.id,
        code_fingerprint=identity["code"],
        configuration_hash=identity["configuration_hash"],
        project_config_revision_id=identity["config_revision_id"],
        index_configuration_hash=build.configuration_hash,
        source_generation=identity["source_generation"],
        semantic_structure_hash=await semantic_snapshot_hash(session, build.project_id, build),
        corpus_fingerprint=build.corpus_fingerprint,
        build_manifest_hash=digest(build.manifest),
        embedding_identity=embedding_identity(build),
        acceptance_set_revision=revision,
        acceptance_set_hash=digest(definition.model_dump(mode="json")),
        report_hash=digest(cases),
        cases=[case],
    )


async def attest_fixture_build(
    session: AsyncSession, project_id: uuid.UUID, build_id: uuid.UUID
) -> None:
    build = await session.get(IndexBuild, build_id)
    assert build is not None and build.project_id == project_id
    artifact = await fixture_artifact(session, build)
    await BuildAcceptanceService(session, project_id).attest(
        build_id,
        artifact,
        actor_id="disposable-test-fixture",
        audit=DatabaseAuditRecorder(session, project_id),
    )
