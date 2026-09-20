"""Document chunk access for retrieval — project-scoped reads."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, func, or_, select, tuple_

from app.models.chunk_keyword_index import ChunkKeywordIndex
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.modules.retrieval.adjacent_selection import (
    ADJACENT_LIMIT,
    MAX_ADJACENT_ANCHORS,
    AdjacentChunkRef,
    page_provenance_is_meaningful,
    page_value,
    select_adjacent_ids,
)
from app.modules.retrieval.source_policy import (
    SOURCE_METADATA_COLUMNS,
    SourceMetadataScope,
    source_metadata_from_row,
)
from app.platform.persistence.filters import not_deleted_filter
from app.platform.persistence.project_scoped_repository import ProjectScopedRepository


class RetrievalChunkRepository(ProjectScopedRepository[DocumentChunk]):
    """Read document chunks within a Project for retrieval workflows."""

    model = DocumentChunk

    async def adjacent_ids(
        self, anchor_ids: list[uuid.UUID], *, index_build_id: uuid.UUID
    ) -> tuple[uuid.UUID, ...]:
        """Structurally neighbouring chunks in one indexed document version.

        Immediate index neighbours and same-section continuations outrank page
        windows. Synthetic Markdown page numbers are not proximity. Table chunks
        on real PDF pages may still expand by page when indices are not consecutive.
        Exclude the anchors themselves so they cannot crowd out missing context.
        """
        if not anchor_ids:
            return ()
        limited = list(dict.fromkeys(anchor_ids))[:MAX_ADJACENT_ANCHORS]
        anchors = await self._indexed_chunk_refs(limited, index_build_id=index_build_id)
        if not anchors:
            return ()
        candidates = await self._indexed_chunk_refs_near_anchors(
            anchors,
            index_build_id=index_build_id,
            exclude_ids=limited,
        )
        return select_adjacent_ids(anchors, candidates)

    async def list_by_document(
        self, document_id: uuid.UUID, *, document_version: int | None = None
    ) -> list[DocumentChunk]:
        """Return all chunks for a document ordered by ``chunk_index``."""
        stmt = (
            self._scoped()
            .where(self.model.document_id == document_id)
            .order_by(self.model.chunk_index)
        )
        if document_version is not None:
            stmt = stmt.where(self.model.document_version == document_version)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def map_by_ids(
        self,
        chunk_ids: list[uuid.UUID],
        *,
        document_id: uuid.UUID | None = None,
    ) -> dict[uuid.UUID, DocumentChunk]:
        """Return chunks keyed by id, optionally scoped to one document."""
        if not chunk_ids:
            return {}
        stmt = self._scoped().where(self.model.id.in_(chunk_ids))
        if document_id is not None:
            stmt = stmt.where(self.model.document_id == document_id)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        return {row.id: row for row in rows}

    async def map_indexed_identities(
        self,
        chunk_ids: list[uuid.UUID],
        *,
        index_build_id: uuid.UUID,
        document_id: uuid.UUID | None = None,
        metadata_filter: dict[str, str] | None = None,
        source_scope: SourceMetadataScope | None = None,
    ) -> dict[uuid.UUID, dict[str, Any]]:
        """Return active-index membership metadata for explicit chunk identities.

        An empty identity list is a restriction: it never scans the corpus.
        """
        if not chunk_ids:
            return {}
        limited = list(dict.fromkeys(chunk_ids))[:24]
        source_columns = (
            [source_scope.selectable.c[name] for name in SOURCE_METADATA_COLUMNS]
            if source_scope is not None and source_scope.selectable is not None
            else []
        )
        stmt = (
            select(self.model.id, ChunkKeywordIndex.metadata_snapshot, *source_columns)
            .join(
                ChunkKeywordIndex,
                (ChunkKeywordIndex.chunk_id == self.model.id)
                & (ChunkKeywordIndex.project_id == self.model.project_id),
            )
            .join(Document, Document.id == self.model.document_id)
            .where(
                self.model.project_id == self._project_id,
                Document.project_id == self._project_id,
                self.model.id.in_(limited),
                ChunkKeywordIndex.project_id == self._project_id,
                ChunkKeywordIndex.index_build_id == index_build_id,
                not_deleted_filter(Document),
            )
        )
        if source_scope is not None and source_scope.selectable is not None:
            stmt = stmt.join(
                source_scope.selectable,
                source_scope.selectable.c.source_document_id == self.model.document_id,
            )
        if document_id is not None:
            stmt = stmt.where(self.model.document_id == document_id)
        if metadata_filter:
            for key, value in metadata_filter.items():
                stmt = stmt.where(ChunkKeywordIndex.metadata_snapshot[key].astext == value)
        result = await self._session.execute(stmt)
        found: dict[uuid.UUID, dict[str, Any]] = {}
        for row in result.all():
            metadata = dict(row.metadata_snapshot or {})
            metadata.update(source_metadata_from_row(row))
            found[row.id] = metadata
        return found

    async def _indexed_chunk_refs(
        self, chunk_ids: list[uuid.UUID], *, index_build_id: uuid.UUID
    ) -> list[AdjacentChunkRef]:
        stmt = (
            select(
                self.model.id,
                self.model.document_id,
                self.model.document_version,
                self.model.chunk_index,
                self.model.page_start,
                self.model.page_end,
                self.model.page_number,
                self.model.chunk_metadata,
            )
            .join(
                ChunkKeywordIndex,
                (ChunkKeywordIndex.chunk_id == self.model.id)
                & (ChunkKeywordIndex.project_id == self.model.project_id),
            )
            .where(
                self.model.project_id == self._project_id,
                self.model.id.in_(chunk_ids),
                ChunkKeywordIndex.project_id == self._project_id,
                ChunkKeywordIndex.index_build_id == index_build_id,
            )
        )
        result = await self._session.execute(stmt)
        return [_chunk_ref(row) for row in result.all()]

    async def _indexed_chunk_refs_near_anchors(
        self,
        anchors: list[AdjacentChunkRef],
        *,
        index_build_id: uuid.UUID,
        exclude_ids: list[uuid.UUID],
    ) -> list[AdjacentChunkRef]:
        if not anchors:
            return []
        document_versions = {(row.document_id, row.document_version) for row in anchors}
        windows: list[Any] = []
        page_col = func.coalesce(self.model.page_start, self.model.page_number)
        page_end_col = func.coalesce(
            self.model.page_end,
            self.model.page_start,
            self.model.page_number,
        )
        for anchor in anchors:
            windows.append(
                and_(
                    self.model.document_id == anchor.document_id,
                    self.model.document_version == anchor.document_version,
                    self.model.chunk_index.between(
                        anchor.chunk_index - ADJACENT_LIMIT,
                        anchor.chunk_index + ADJACENT_LIMIT,
                    ),
                )
            )
            if page_provenance_is_meaningful(anchor):
                page = page_value(anchor)
                if page is not None:
                    page_end = anchor.page_end if anchor.page_end is not None else page
                    page_low, page_high = min(page, page_end), max(page, page_end)
                    windows.append(
                        and_(
                            self.model.document_id == anchor.document_id,
                            self.model.document_version == anchor.document_version,
                            page_col <= page_high + 1,
                            page_end_col >= page_low - 1,
                        )
                    )
        stmt = (
            select(
                self.model.id,
                self.model.document_id,
                self.model.document_version,
                self.model.chunk_index,
                self.model.page_start,
                self.model.page_end,
                self.model.page_number,
                self.model.chunk_metadata,
            )
            .join(
                ChunkKeywordIndex,
                (ChunkKeywordIndex.chunk_id == self.model.id)
                & (ChunkKeywordIndex.project_id == self.model.project_id),
            )
            .where(
                self.model.project_id == self._project_id,
                tuple_(self.model.document_id, self.model.document_version).in_(document_versions),
                or_(*windows),
                self.model.id.not_in(exclude_ids),
                ChunkKeywordIndex.project_id == self._project_id,
                ChunkKeywordIndex.index_build_id == index_build_id,
            )
        )
        result = await self._session.execute(stmt)
        return [_chunk_ref(row) for row in result.all()]


def _chunk_ref(row: Any) -> AdjacentChunkRef:
    metadata = row.chunk_metadata if isinstance(row.chunk_metadata, dict) else {}
    return AdjacentChunkRef(
        id=row.id,
        document_id=row.document_id,
        document_version=row.document_version,
        chunk_index=row.chunk_index,
        page_start=row.page_start,
        page_end=row.page_end,
        page_number=row.page_number,
        metadata=metadata,
    )
