"""Read immutable parsed artifacts into build-owned chunks without touching sources."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk_keyword_index import ChunkKeywordIndex
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.modules.knowledge.domain.document_storage_keys import build_parsed_json_storage_key
from app.modules.knowledge.services.chunking.models import DraftChunk
from app.modules.knowledge.services.chunking_service import ChunkingService
from app.platform.providers.contracts.document_parser import (
    ParsedDocument,
    ParsedElement,
    ParsedElementType,
    SourceFormat,
)
from app.platform.providers.contracts.storage import BaseStorageProvider


class PrivateStructuralGeneration:
    def __init__(
        self,
        session: AsyncSession,
        storage: BaseStorageProvider,
        chunking: ChunkingService,
        source_build_id: uuid.UUID | None = None,
    ) -> None:
        self.source_build_id = source_build_id
        self.session = session
        self.storage = storage
        self.chunking = chunking

    async def prepare(self, document: Document, build_id: uuid.UUID) -> list[DocumentChunk]:
        key = build_parsed_json_storage_key(
            project_id=document.project_id, document_id=document.id, version=document.version
        )
        raw = b"".join([part async for part in self.storage.get(key)])
        data = json.loads(raw)
        data.pop("chunking_run", None)
        data["elements"] = tuple(
            ParsedElement(**{**item, "element_type": ParsedElementType(item["element_type"])})
            for item in data.get("elements", [])
        )
        data["source_format"] = SourceFormat(data.get("source_format", "unknown"))
        parsed = ParsedDocument(**data)
        if self.source_build_id:
            return await self._copy_final_rows(document, build_id, parsed)
        chunks, _ = await self.chunking.split_document(parsed)
        # Workflow clears its own unsealed index rows before this retry cleanup.
        await self.session.execute(
            delete(DocumentChunk).where(
                DocumentChunk.project_id == document.project_id,
                DocumentChunk.document_id == document.id,
                DocumentChunk.generation_id == build_id,
            )
        )
        rows = [
            DocumentChunk(
                id=uuid.uuid4(),
                project_id=document.project_id,
                document_id=document.id,
                document_version=document.version,
                generation_id=build_id,
                chunk_index=c.chunk_index,
                content=c.content,
                token_count=c.token_count,
                char_start=c.char_start,
                char_end=c.char_end,
                page_number=c.page_number,
                page_start=c.page_start,
                page_end=c.page_end,
                chunk_metadata=c.chunk_metadata,
            )
            for c in chunks
        ]
        self.session.add_all(rows)
        await self.session.flush()
        return rows

    async def _copy_final_rows(
        self, document: Document, build_id: uuid.UUID, parsed: ParsedDocument
    ) -> list[DocumentChunk]:
        sources = (
            (
                await self.session.execute(
                    select(DocumentChunk)
                    .join(
                        ChunkKeywordIndex,
                        (ChunkKeywordIndex.chunk_id == DocumentChunk.id)
                        & (ChunkKeywordIndex.project_id == DocumentChunk.project_id),
                    )
                    .where(
                        ChunkKeywordIndex.index_build_id == self.source_build_id,
                        ChunkKeywordIndex.project_id == document.project_id,
                        DocumentChunk.document_id == document.id,
                        DocumentChunk.document_version == document.version,
                    )
                    .order_by(DocumentChunk.chunk_index)
                )
            )
            .scalars()
            .all()
        )
        drafts = [
            DraftChunk(
                content=source.content,
                char_start=source.char_start,
                char_end=source.char_end,
                page_start=source.page_start,
                page_end=source.page_end,
                chunk_order=source.chunk_index,
                metadata=dict(source.chunk_metadata),
            )
            for source in sources
        ]
        await self.session.execute(
            delete(DocumentChunk).where(
                DocumentChunk.project_id == document.project_id,
                DocumentChunk.document_id == document.id,
                DocumentChunk.generation_id == build_id,
            )
        )
        self.chunking.reattest_final_drafts(drafts, parsed)
        rows = []
        for source, draft in zip(sources, drafts, strict=True):
            if draft.content != source.content:
                raise ValueError("Revalidation may not change vector input text.")
            draft.metadata["revalidation_source_chunk_id"] = str(source.id)
            rows.append(
                DocumentChunk(
                    id=uuid.uuid4(),
                    project_id=document.project_id,
                    document_id=document.id,
                    document_version=document.version,
                    generation_id=build_id,
                    chunk_index=source.chunk_index,
                    content=source.content,
                    token_count=source.token_count,
                    char_start=draft.char_start,
                    char_end=draft.char_end,
                    page_number=source.page_number,
                    page_start=source.page_start,
                    page_end=source.page_end,
                    chunk_metadata=draft.metadata,
                )
            )
        self.session.add_all(rows)
        await self.session.flush()
        return rows
