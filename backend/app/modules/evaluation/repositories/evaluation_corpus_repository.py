"""Stable fingerprint of the indexed corpus visible to an evaluation run."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.models.chunk_embedding import ChunkEmbedding
from app.models.chunk_keyword_index import ChunkKeywordIndex
from app.models.index_build import IndexBuild, IndexBuildState, ProjectIndexPointer


class EvaluationCorpusRepository:
    """Describe the exact vector and keyword rows used by quality comparisons."""

    def __init__(self, session: AsyncSession, project_id: uuid.UUID) -> None:
        self._session = session
        self._project_id = project_id

    async def snapshot(
        self,
        *,
        embedding_set_version: int,
        embedding_provider: str,
        embedding_model: str,
        preview_index_build_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        active_build_id = await self._session.scalar(
            select(ProjectIndexPointer.active_build_id).where(
                ProjectIndexPointer.project_id == self._project_id
            )
        )
        if preview_index_build_id is not None:
            build = await self._session.scalar(
                select(IndexBuild).where(
                    IndexBuild.project_id == self._project_id,
                    IndexBuild.id == preview_index_build_id,
                )
            )
            if (
                build is None
                or build.state != IndexBuildState.VALIDATED
                or build.validated_at is None
            ):
                raise BadRequestError(
                    "Candidate evaluation requires a sealed project build.",
                    code="preview_build_unavailable",
                )
            from app.platform.domain.structural_contract import verify_structural_build
            from app.platform.jobs.errors import PermanentJobError

            try:
                await verify_structural_build(self._session, self._project_id, build)
            except PermanentJobError as exc:
                raise BadRequestError(exc.message, code="preview_build_unavailable") from exc
            manifest = build.manifest or {}
            if (
                manifest.get("embedding_provider") != embedding_provider
                or manifest.get("embedding_model") != embedding_model
                or build.embedding_set_version != embedding_set_version
            ):
                raise BadRequestError(
                    "Candidate embedding identity differs from the evaluation configuration.",
                    code="preview_embedding_mismatch",
                )
            active_build_id = build.id
        semantic_rows = (
            await self._session.execute(
                select(
                    ChunkEmbedding.chunk_id,
                    ChunkEmbedding.document_id,
                    ChunkEmbedding.document_version,
                    ChunkEmbedding.input_content_hash,
                )
                .where(
                    ChunkEmbedding.project_id == self._project_id,
                    ChunkEmbedding.index_build_id == active_build_id,
                    ChunkEmbedding.embedding_set_version == embedding_set_version,
                    ChunkEmbedding.provider == embedding_provider,
                    ChunkEmbedding.model == embedding_model,
                )
                .order_by(ChunkEmbedding.chunk_id)
            )
        ).all()
        keyword_rows = (
            await self._session.execute(
                select(
                    ChunkKeywordIndex.chunk_id,
                    ChunkKeywordIndex.document_id,
                    ChunkKeywordIndex.document_version,
                )
                .where(
                    ChunkKeywordIndex.project_id == self._project_id,
                    ChunkKeywordIndex.index_build_id == active_build_id,
                    ChunkKeywordIndex.embedding_set_version == embedding_set_version,
                )
                .order_by(ChunkKeywordIndex.chunk_id)
            )
        ).all()
        manifest = {
            "semantic": [
                [
                    str(row.chunk_id),
                    str(row.document_id),
                    row.document_version,
                    row.input_content_hash,
                ]
                for row in semantic_rows
            ],
            "keyword": [
                [str(row.chunk_id), str(row.document_id), row.document_version]
                for row in keyword_rows
            ],
        }
        from app.models.index_scope_review import IndexScopeReview

        reviews = (
            await self._session.scalars(
                select(IndexScopeReview)
                .where(
                    IndexScopeReview.project_id == self._project_id,
                    IndexScopeReview.build_id == active_build_id,
                )
                .order_by(IndexScopeReview.chunk_id)
            )
        ).all()
        if reviews:
            manifest["scope_reviews"] = [[str(v.chunk_id), v.review_hash] for v in reviews]
        chunk_ids = {str(row.chunk_id) for row in semantic_rows} | {
            str(row.chunk_id) for row in keyword_rows
        }
        document_ids = {str(row.document_id) for row in semantic_rows} | {
            str(row.document_id) for row in keyword_rows
        }
        return {
            "fingerprint": _digest(manifest),
            "document_count": len(document_ids),
            "indexed_chunk_count": len(chunk_ids),
            "semantic_chunk_count": len(semantic_rows),
            "keyword_chunk_count": len(keyword_rows),
            "embedding_set_version": embedding_set_version,
            "embedding_provider": embedding_provider,
            "embedding_model": embedding_model,
            "index_build_id": str(active_build_id) if active_build_id else None,
        }


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
