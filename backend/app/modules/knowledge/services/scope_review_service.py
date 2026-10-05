"""Publish exact-source review overlays under a sealed candidate's lock."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.chunk_keyword_index import ChunkKeywordIndex
from app.models.document_chunk import DocumentChunk
from app.models.index_build import IndexBuild, IndexBuildState
from app.models.index_scope_review import IndexScopeReview
from app.models.project import Project
from app.models.source_metadata import SourceMetadataRevision
from app.modules.knowledge.schemas.scope_review import ScopeReviewCreate
from app.modules.knowledge.scope_facts import extract_scope_facts
from app.modules.knowledge.source_metadata_read import _canonical_source_scope
from app.platform.audit.contracts import AuditActorType, AuditEventType, AuditOutcome, AuditRecorder
from app.platform.domain.content_hash import content_hash
from app.platform.domain.source_scope import span_hash, validated_scope_envelope


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class ScopeReviewService:
    def __init__(self, session: AsyncSession, project_id: uuid.UUID):
        self.session = session
        self.project_id = project_id

    async def publish(
        self,
        build_id: uuid.UUID,
        request: ScopeReviewCreate,
        *,
        actor_id: str,
        audit: AuditRecorder,
    ) -> IndexScopeReview:
        build = await self.session.scalar(
            select(IndexBuild)
            .where(IndexBuild.project_id == self.project_id, IndexBuild.id == build_id)
            .with_for_update()
        )
        if build is None:
            raise NotFoundError("Index build not found.", code="index_build_not_found")
        if build.state != IndexBuildState.VALIDATED or build.validated_at is None:
            raise BadRequestError(
                "Review publication requires an unactivated sealed candidate.",
                code="scope_review_build_unavailable",
            )
        project = await self.session.get(Project, self.project_id, with_for_update=True)
        if project is None or request.source_generation != project.source_metadata_generation:
            raise BadRequestError("Source generation changed.", code="scope_review_stale")
        chunk = await self.session.scalar(
            select(DocumentChunk)
            .join(ChunkKeywordIndex, ChunkKeywordIndex.chunk_id == DocumentChunk.id)
            .where(
                DocumentChunk.project_id == self.project_id,
                ChunkKeywordIndex.project_id == self.project_id,
                ChunkKeywordIndex.index_build_id == build_id,
                DocumentChunk.id == request.chunk_id,
            )
        )
        revision = await self.session.scalar(
            select(SourceMetadataRevision).where(
                SourceMetadataRevision.project_id == self.project_id,
                SourceMetadataRevision.id == request.source_revision_id,
            )
        )
        if chunk is None or revision is None or revision.document_id != chunk.document_id:
            raise BadRequestError(
                "Review source is not a member of this project/build.",
                code="scope_review_source_mismatch",
            )
        scope = _canonical_source_scope(
            project_id=self.project_id,
            generation=request.source_generation,
            reference_date=datetime.now(UTC).date(),
            historical=False,
        )
        pinned_revision = await self.session.scalar(
            select(scope.c.source_revision_id).where(
                scope.c.source_document_id == chunk.document_id
            )
        )
        if (
            pinned_revision != revision.id
            or revision.content_hash != request.source_content_hash
            or content_hash(chunk.content) != request.chunk_hash
        ):
            raise BadRequestError(
                "Review source revision or content changed.", code="scope_review_stale"
            )
        facts = []
        for fact in request.facts:
            span = fact.source_span
            # Offsets are document offsets, bound to this exact final chunk.
            start, end = span.get("char_start"), span.get("char_end")
            if (
                chunk.char_start is None
                or type(start) is not int
                or type(end) is not int
                or chunk.content[start - chunk.char_start : end - chunk.char_start]
                != span.get("text")
                or start < chunk.char_start
                or end > chunk.char_start + len(chunk.content)
            ):
                raise BadRequestError(
                    "Review requires exact chunk-relative source offsets.",
                    code="scope_review_invalid_span",
                )
            if (
                fact.status != "reviewed"
                or not fact.review_provenance.get("reason", "").strip()
                or span.get("provenance") != "exact_source_span"
                or fact.review_provenance.get("evidence_hash")
                != span_hash(str(span.get("text", "")))
            ):
                raise BadRequestError(
                    "Review requires an explicit reason.", code="scope_review_invalid"
                )
            extracted = extract_scope_facts([span], unit_id=fact.locality_id)
            if (
                fact.kind == "period"
                and fact.legal_kind is not None
                and not any(
                    v["kind"] == "period"
                    and v["legal_kind"] == fact.legal_kind
                    and v["start_year"] == fact.start_year
                    and v["end_year"] == fact.end_year
                    for v in extracted
                )
            ):
                raise BadRequestError(
                    "Typed period does not occur in the exact reviewed span.",
                    code="scope_review_invalid_span",
                )
            if (
                fact.kind == "provision"
                and fact.value.casefold() not in str(span.get("text", "")).casefold()
            ):
                raise BadRequestError(
                    "Provision does not occur in the exact reviewed span.",
                    code="scope_review_invalid_span",
                )
            facts.append(
                fact.model_copy(
                    update={"review_provenance": {**fact.review_provenance, "reviewer": actor_id}}
                ).model_dump(mode="json")
            )
        envelope = {"scope_fact_version": "scope.v2", "scope_facts": facts}
        if not validated_scope_envelope(envelope, chunk.content):
            raise BadRequestError("Review envelope is invalid.", code="scope_review_invalid")
        value_hash = digest(
            {"request": request.model_dump(mode="json"), "envelope": envelope, "actor": actor_id}
        )
        existing = await self.session.scalar(
            select(IndexScopeReview).where(
                IndexScopeReview.project_id == self.project_id,
                IndexScopeReview.build_id == build_id,
                IndexScopeReview.chunk_id == chunk.id,
            )
        )
        if existing:
            if existing.review_hash != value_hash:
                raise BadRequestError(
                    "Published review is immutable; use a new private candidate.",
                    code="scope_review_immutable",
                )
            return existing
        row = IndexScopeReview(
            id=uuid.uuid4(),
            project_id=self.project_id,
            build_id=build_id,
            chunk_id=chunk.id,
            source_revision_id=revision.id,
            source_generation=request.source_generation,
            chunk_hash=request.chunk_hash,
            review_hash=value_hash,
            envelope=envelope,
            created_by=actor_id,
        )
        self.session.add(row)
        audit.record(
            event_type=AuditEventType.SOURCE_METADATA_REVISION_CREATED,
            actor_type=AuditActorType.OPERATOR,
            actor_id=actor_id,
            resource_type="index_scope_review",
            resource_id=row.id,
            outcome=AuditOutcome.SUCCESS,
            detail={"build_id": str(build_id), "review_hash": value_hash},
        )
        await self.session.commit()
        await self.session.refresh(row)
        return row
