"""Persisted structural intent validated independently of worker claims and row counts."""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk_embedding import EMBEDDING_SCHEMA_VERSION, ChunkEmbedding
from app.models.chunk_keyword_index import ChunkKeywordIndex
from app.models.document_chunk import DocumentChunk
from app.models.index_build import IndexBuild
from app.platform.domain.content_hash import content_hash
from app.platform.domain.source_scope import validated_scope_envelope
from app.platform.jobs.errors import PermanentJobError

STRUCTURAL_CONTRACT_VERSION = "structure.v1"


def validate_structural_manifest(build: IndexBuild) -> None:
    intent = build.structural_contract_version
    if not intent:
        return
    entries = build.manifest.get("documents", [])
    if (
        intent != STRUCTURAL_CONTRACT_VERSION
        or not entries
        or any(entry.get("chunk_generation_id") != str(build.id) for entry in entries)
    ):
        raise PermanentJobError(
            "Build did not satisfy its structural generation intent.",
            code="structural_build_contract_invalid",
        )


async def verify_structural_build(
    session: AsyncSession,
    project_id: uuid.UUID,
    build: IndexBuild,
    *,
    allow_legacy_unit_hash: bool = False,
) -> None:
    validate_structural_manifest(build)
    if not build.structural_contract_version:
        return
    if build.project_id != project_id:
        raise PermanentJobError(
            "Structural build Project mismatch.", code="structural_build_contract_invalid"
        )
    rows = (
        (
            await session.execute(
                select(DocumentChunk)
                .join(
                    ChunkKeywordIndex,
                    (ChunkKeywordIndex.chunk_id == DocumentChunk.id)
                    & (ChunkKeywordIndex.project_id == DocumentChunk.project_id),
                )
                .where(
                    ChunkKeywordIndex.project_id == project_id,
                    ChunkKeywordIndex.index_build_id == build.id,
                )
            )
        )
        .scalars()
        .all()
    )
    expected = {
        (str(e["document_id"]), int(e["document_version"])): int(e["chunk_count"])
        for e in build.manifest["documents"]
    }
    actual: dict[tuple[str, int], int] = {}
    for row in rows:
        metadata = row.chunk_metadata
        key = (str(row.document_id), row.document_version)
        if (
            row.generation_id != build.id
            or key not in expected
            or metadata.get("structure_version") != STRUCTURAL_CONTRACT_VERSION
            or (
                not allow_legacy_unit_hash
                and metadata.get("structural_unit_id")
                != hashlib.sha256(row.content.encode()).hexdigest()
            )
            or not isinstance(metadata.get("source_spans"), list)
            or any(
                not isinstance(span, dict) or str(span.get("text", "")) not in row.content
                for span in metadata.get("source_spans", [])
            )
        ):
            raise PermanentJobError(
                "Structural row provenance is incompatible with the manifest.",
                code="structural_build_contract_invalid",
            )
        actual[key] = actual.get(key, 0) + 1
    vectors = (
        (
            await session.execute(
                select(ChunkEmbedding).where(
                    ChunkEmbedding.project_id == project_id,
                    ChunkEmbedding.index_build_id == build.id,
                )
            )
        )
        .scalars()
        .all()
    )
    chunks = {row.id: row for row in rows}
    manifest = build.manifest
    if len(vectors) != len(rows) or {vector.chunk_id for vector in vectors} != set(chunks):
        raise PermanentJobError(
            "Structural vector membership is incomplete.", code="structural_build_contract_invalid"
        )
    for vector in vectors:
        chunk = chunks[vector.chunk_id]
        if (
            vector.input_content_hash != content_hash(chunk.content)
            or vector.document_version != chunk.document_version
            or vector.embedding_schema_version != EMBEDDING_SCHEMA_VERSION
            or vector.provider != manifest.get("embedding_provider")
            or vector.model != manifest.get("embedding_model")
            or vector.dimensions != manifest.get("embedding_dimensions")
            or vector.embedding_set_version != manifest.get("embedding_set_version")
        ):
            raise PermanentJobError(
                "Structural vector identity or input differs from final text.",
                code="structural_build_contract_invalid",
            )
    if actual != expected or len(rows) != build.chunk_count:
        raise PermanentJobError(
            "Structural generation membership is incomplete.",
            code="structural_build_contract_invalid",
        )


async def verify_semantic_scope_snapshot(
    session: AsyncSession, project_id: uuid.UUID, build: IndexBuild
) -> None:
    """Validate v2 scope proof without rewriting any legacy chunks or source metadata."""
    rows = (
        (
            await session.execute(
                select(DocumentChunk)
                .join(
                    ChunkKeywordIndex,
                    (ChunkKeywordIndex.chunk_id == DocumentChunk.id)
                    & (ChunkKeywordIndex.project_id == DocumentChunk.project_id),
                )
                .where(
                    ChunkKeywordIndex.project_id == project_id,
                    ChunkKeywordIndex.index_build_id == build.id,
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        raw = row.chunk_metadata.get("scope_facts", [])
        claims_v2 = row.chunk_metadata.get("scope_fact_version") == "scope.v2" or (
            isinstance(raw, list)
            and any(isinstance(fact, dict) and fact.get("version") == "scope.v2" for fact in raw)
        )
        if claims_v2 and (
            not isinstance(raw, list)
            or len(validated_scope_envelope(row.chunk_metadata, row.content)) != len(raw)
        ):
            raise PermanentJobError(
                "Semantic scope provenance is invalid.",
                code="structural_scope_contract_invalid",
                context={"chunk_id": str(row.id)},
            )
