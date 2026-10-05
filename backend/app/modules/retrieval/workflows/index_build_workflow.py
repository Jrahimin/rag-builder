"""Isolated full-corpus vector and keyword snapshot construction."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk_embedding import EMBEDDING_SCHEMA_VERSION, ChunkEmbedding
from app.models.chunk_keyword_index import ChunkKeywordIndex
from app.models.document import Document, DocumentStatus
from app.models.document_chunk import DocumentChunk
from app.models.index_build import IndexBuild, IndexBuildState, ProjectIndexPointer
from app.models.keyword_term_stats import KeywordCollectionStats, KeywordTermStats
from app.modules.retrieval.build_acceptance import require_build_acceptance
from app.modules.retrieval.keyword.fts import to_search_vector
from app.modules.retrieval.keyword.tokenizer import (
    normalize_for_indexing,
    term_frequencies,
    tokenize,
)
from app.modules.retrieval.repositories.index_build_repository import IndexBuildRepository
from app.modules.retrieval.structural_contract import (
    verify_semantic_scope_snapshot,
    verify_structural_build,
)
from app.platform.db.advisory_lock import acquire_project_stage_lock
from app.platform.domain.content_hash import content_hash
from app.platform.domain.language_detection import (
    LANGUAGE_METADATA_SCHEMA_VERSION,
    ROUTING_LANGUAGE_MIXED,
    ROUTING_LANGUAGE_UNKNOWN,
    build_index_language_snapshot,
    normalize_routing_language,
)
from app.platform.jobs.contracts import JobProgressCallback
from app.platform.jobs.errors import JobError, PermanentJobError
from app.platform.providers.contracts.embedding import (
    BaseEmbeddingProvider,
    EmbeddingBatchResult,
    EmbeddingPurpose,
)
from app.platform.providers.provider_work import cache_identity


class IndexBuildWorkflow:
    """Build and validate one immutable retrieval snapshot before activation."""

    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        embedder: BaseEmbeddingProvider,
        *,
        embedding_set_version: int,
        batch_size: int,
        filterable_metadata_keys: list[str],
        fts_regconfig: str,
        on_progress: JobProgressCallback | None = None,
        reuse_index_build_id: uuid.UUID | None = None,
        private_chunk_factory: Callable[[Document, uuid.UUID], Awaitable[list[DocumentChunk]]]
        | None = None,
    ) -> None:
        self._reuse_index_build_id = reuse_index_build_id
        self._private_chunk_factory = private_chunk_factory
        self._session = session
        self._project_id = project_id
        self._embedder = embedder
        self._embedding_set_version = embedding_set_version
        self._batch_size = batch_size
        self._filterable_metadata_keys = filterable_metadata_keys
        self._fts_regconfig = fts_regconfig
        self._on_progress = on_progress
        self._builds = IndexBuildRepository(session, project_id)

    async def run(
        self,
        build_id: uuid.UUID,
        *,
        exclude_document_id: uuid.UUID | None = None,
        auto_activate: bool = False,
    ) -> IndexBuild:
        if self._private_chunk_factory is not None and auto_activate:
            raise PermanentJobError(
                "Private structural builds require explicit acceptance and activation.",
                code="private_build_auto_activation_forbidden",
            )
        build = await self._builds.get_by_id(build_id, for_update=True)
        if build is None:
            raise PermanentJobError("Index build does not exist.", code="index_build_not_found")
        if build.state in {
            IndexBuildState.VALIDATED,
            IndexBuildState.ACTIVE,
            IndexBuildState.RETAINED,
        }:
            await verify_structural_build(self._session, self._project_id, build)
            return build
        if build.state is not IndexBuildState.BUILDING:
            raise PermanentJobError(
                "Index build is no longer writable.", code="index_build_immutable"
            )

        if build.structural_contract_version and self._private_chunk_factory is None:
            raise PermanentJobError(
                "Structural intent requires a compatible producer.",
                code="structural_worker_incompatible",
            )
        embedding_origin: dict[str, object] | None = cache_identity(
            self._embedder, self._embedding_set_version, EmbeddingPurpose.DOCUMENT
        )
        if self._reuse_index_build_id:
            source_build = await self._builds.get_by_id(self._reuse_index_build_id)
            if (
                source_build is None
                or source_build.state not in {IndexBuildState.VALIDATED, IndexBuildState.RETAINED}
                or not source_build.structural_contract_version
            ):
                raise PermanentJobError(
                    "Revalidation input is not a sealed structural build.",
                    code="structural_build_contract_invalid",
                )
            await verify_structural_build(
                self._session, self._project_id, source_build, allow_legacy_unit_hash=True
            )
            await self._validate_versions(source_build.manifest["documents"])
            # Preserve source origin; a current endpoint is not proof of vector origin.
            # Unlabeled legacy source vectors remain unproven after revalidation.
            embedding_origin = source_build.manifest.get("embedding_origin")
        await self._clear_partial_rows(build.id)
        documents = await self._eligible_documents(exclude_document_id=exclude_document_id)
        manifest: list[dict[str, object]] = []
        all_chunks: list[tuple[Document, DocumentChunk]] = []
        for document in documents:
            chunks = (
                await self._private_chunk_factory(document, build.id)
                if self._private_chunk_factory
                else await self._current_chunks(document)
            )
            if not chunks:
                continue
            manifest.append(
                {
                    "document_id": str(document.id),
                    "document_version": document.version,
                    "chunk_count": len(chunks),
                    "chunk_generation_id": str(build.id)
                    if self._private_chunk_factory
                    else (
                        str(document.chunk_generation_id) if document.chunk_generation_id else None
                    ),
                    **_document_language_manifest_fields(document, chunks),
                }
            )
            all_chunks.extend((document, chunk) for chunk in chunks)

        await self._report("building_vectors", 10)
        for offset in range(0, len(all_chunks), self._batch_size):
            batch = all_chunks[offset : offset + self._batch_size]
            if self._reuse_index_build_id:
                source_ids = [
                    uuid.UUID(str(chunk.chunk_metadata["revalidation_source_chunk_id"]))
                    for _, chunk in batch
                ]
                source_vectors = (
                    (
                        await self._session.execute(
                            select(ChunkEmbedding).where(
                                ChunkEmbedding.project_id == self._project_id,
                                ChunkEmbedding.index_build_id == self._reuse_index_build_id,
                                ChunkEmbedding.chunk_id.in_(source_ids),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                vectors_by_id = {vector.chunk_id: vector for vector in source_vectors}
                expected_identity = (
                    self._embedder.provider_name,
                    self._embedder.model_name,
                    self._embedder.dimensions,
                    self._embedding_set_version,
                )
                ordered_vectors = []
                for source_id, (_, chunk) in zip(source_ids, batch, strict=True):
                    vector = vectors_by_id.get(source_id)
                    if (
                        vector is None
                        or vector.input_content_hash != content_hash(chunk.content)
                        or (
                            vector.provider,
                            vector.model,
                            vector.dimensions,
                            vector.embedding_set_version,
                        )
                        != expected_identity
                        or vector.embedding_schema_version != EMBEDDING_SCHEMA_VERSION
                    ):
                        raise PermanentJobError(
                            "Revalidation vector input or identity changed.",
                            code="revalidation_vector_mismatch",
                        )
                    ordered_vectors.append(vector)
                result = EmbeddingBatchResult(
                    vectors=[list(v.embedding) for v in ordered_vectors],
                    provider=ordered_vectors[0].provider,
                    model=ordered_vectors[0].model,
                    dimensions=ordered_vectors[0].dimensions,
                    provider_version=ordered_vectors[0].provider_version,
                )
            else:
                result = await self._embedder.embed_texts(
                    [chunk.content for _, chunk in batch],
                    purpose=EmbeddingPurpose.DOCUMENT,
                )
            if len(result.vectors) != len(batch) or any(
                len(vector) != result.dimensions for vector in result.vectors
            ):
                raise PermanentJobError(
                    "Embedding provider returned an invalid vector batch.",
                    code="index_build_embedding_mismatch",
                )
            self._session.add_all(
                [
                    ChunkEmbedding(
                        project_id=self._project_id,
                        index_build_id=build.id,
                        document_id=document.id,
                        chunk_id=chunk.id,
                        embedding_set_version=self._embedding_set_version,
                        document_version=document.version,
                        provider=result.provider,
                        model=result.model,
                        dimensions=result.dimensions,
                        provider_version=result.provider_version,
                        input_content_hash=content_hash(chunk.content),
                        embedding_schema_version=EMBEDDING_SCHEMA_VERSION,
                        embedding=vector,
                    )
                    for (document, chunk), vector in zip(batch, result.vectors, strict=True)
                ]
            )
            progress = 10 + int(45 * (offset + len(batch)) / max(len(all_chunks), 1))
            await self._report("building_vectors", min(progress, 55))

        await self._report("building_keyword_snapshot", 60)
        for index, (document, chunk) in enumerate(all_chunks, start=1):
            normalized = normalize_for_indexing(chunk.content)
            tokens = tokenize(chunk.content)
            metadata = {
                key: str(chunk.chunk_metadata[key])
                for key in self._filterable_metadata_keys
                if key in chunk.chunk_metadata
            }
            metadata.update(
                {
                    key: chunk.chunk_metadata[key]
                    for key in (
                        "structure_version",
                        "structural_unit_id",
                        "scope_facts",
                        "scope_fact_version",
                        "source_spans",
                    )
                    if key in chunk.chunk_metadata
                }
            )
            metadata.update(
                build_index_language_snapshot(
                    content=chunk.content,
                    chunk_metadata=chunk.chunk_metadata,
                    document_language=document.language,
                )
            )
            self._session.add(
                ChunkKeywordIndex(
                    project_id=self._project_id,
                    index_build_id=build.id,
                    document_id=document.id,
                    chunk_id=chunk.id,
                    embedding_set_version=self._embedding_set_version,
                    document_version=document.version,
                    content_normalized=normalized,
                    token_count=len(tokens),
                    term_frequencies=term_frequencies(tokens),
                    metadata_snapshot=metadata,
                    search_vector=to_search_vector(self._fts_regconfig, normalized),
                )
            )
            if index % 100 == 0:
                await self._report(
                    "building_keyword_snapshot",
                    min(60 + int(20 * index / max(len(all_chunks), 1)), 80),
                )

        await self._session.flush()
        await self._rebuild_statistics(build.id)
        await self._validate_versions(manifest)

        count = len(all_chunks)
        language_inventory = _language_inventory(all_chunks)
        build.document_count = len(manifest)
        build.chunk_count = count
        build.vector_count = count
        build.keyword_count = count
        build.manifest = {
            "artifact_fingerprint_version": build.artifact_fingerprint_version,
            "artifact_fingerprint": build.artifact_fingerprint,
            "index_profile_id": build.index_profile_id or "legacy-unprofiled",
            "index_profile_hash": build.index_profile_hash,
            "documents": manifest,
            "structural_contract_version": build.structural_contract_version,
            "revalidation_source_build_id": str(self._reuse_index_build_id)
            if self._reuse_index_build_id
            else None,
            "language_metadata_schema_version": LANGUAGE_METADATA_SCHEMA_VERSION,
            "chunk_language_counts": language_inventory["chunk_language_counts"],
            "document_language_counts": language_inventory["document_language_counts"],
            "embedding_origin": embedding_origin,
            "embedding_set_version": self._embedding_set_version,
            "embedding_provider": self._embedder.provider_name,
            "embedding_model": self._embedder.model_name,
            "embedding_dimensions": self._embedder.dimensions,
        }
        build.corpus_fingerprint = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        await self._session.flush()
        await verify_structural_build(self._session, self._project_id, build)
        await verify_semantic_scope_snapshot(self._session, self._project_id, build)
        build.validated_at = datetime.now(UTC)
        build.state = IndexBuildState.VALIDATED
        await self._report("validated", 90)
        if auto_activate:
            try:
                await activate_index_build(self._session, self._project_id, build)
            except PermanentJobError as exc:
                if exc.code != "index_acceptance_missing":
                    raise
                # A sealed candidate is successful build work, not permission to publish.
                await self._report("validated_pending_quality_acceptance", 100)
            else:
                await self._report("active", 100)
        return build

    async def _eligible_documents(self, *, exclude_document_id: uuid.UUID | None) -> list[Document]:
        stmt = (
            select(Document)
            .where(
                Document.project_id == self._project_id,
                Document.deleted_at.is_(None),
                Document.status.in_(
                    [
                        DocumentStatus.CHUNKED,
                        DocumentStatus.EMBEDDED,
                        DocumentStatus.READY,
                        DocumentStatus.EMBEDDING,
                        DocumentStatus.INDEXING,
                    ]
                ),
            )
            .order_by(Document.id)
        )
        if exclude_document_id is not None:
            stmt = stmt.where(Document.id != exclude_document_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def _current_chunks(self, document: Document) -> list[DocumentChunk]:
        result = await self._session.execute(
            select(DocumentChunk)
            .where(
                DocumentChunk.project_id == self._project_id,
                DocumentChunk.document_id == document.id,
                DocumentChunk.document_version == document.version,
                DocumentChunk.generation_id == document.chunk_generation_id,
            )
            .order_by(DocumentChunk.chunk_index)
        )
        return list(result.scalars().all())

    async def _clear_partial_rows(self, build_id: uuid.UUID) -> None:
        for model in (ChunkEmbedding, ChunkKeywordIndex, KeywordTermStats, KeywordCollectionStats):
            await self._session.execute(
                delete(model).where(
                    model.project_id == self._project_id, model.index_build_id == build_id
                )
            )

    async def _rebuild_statistics(self, build_id: uuid.UUID) -> None:
        result = await self._session.execute(
            select(
                ChunkKeywordIndex.document_id,
                ChunkKeywordIndex.term_frequencies,
                ChunkKeywordIndex.token_count,
            ).where(
                ChunkKeywordIndex.project_id == self._project_id,
                ChunkKeywordIndex.index_build_id == build_id,
            )
        )
        rows = result.all()
        documents_by_term: dict[str, set[uuid.UUID]] = {}
        document_ids: set[uuid.UUID] = set()
        total_tokens = 0
        for row in rows:
            document_ids.add(row.document_id)
            total_tokens += row.token_count
            for term in row.term_frequencies:
                documents_by_term.setdefault(term, set()).add(row.document_id)
        self._session.add_all(
            [
                KeywordTermStats(
                    project_id=self._project_id,
                    index_build_id=build_id,
                    embedding_set_version=self._embedding_set_version,
                    term=term,
                    document_frequency=len(ids),
                )
                for term, ids in documents_by_term.items()
            ]
        )
        self._session.add(
            KeywordCollectionStats(
                project_id=self._project_id,
                index_build_id=build_id,
                embedding_set_version=self._embedding_set_version,
                total_documents=len(document_ids),
                total_chunks=len(rows),
                avg_doc_length=(total_tokens / len(rows)) if rows else 1.0,
            )
        )

    async def _validate_versions(self, manifest: list[dict[str, object]]) -> None:
        for item in manifest:
            document_id = uuid.UUID(str(item["document_id"]))
            raw_version = item["document_version"]
            if not isinstance(raw_version, int):
                raise PermanentJobError(
                    "Index build manifest has an invalid document version.",
                    code="index_build_manifest_invalid",
                )
            expected = raw_version
            actual = await self._session.scalar(
                select(Document.version).where(
                    Document.project_id == self._project_id, Document.id == document_id
                )
            )
            if actual != expected:
                raise JobError(
                    "Corpus changed while the isolated build was running.",
                    code="index_build_corpus_changed",
                    retryable=True,
                    context={
                        "document_id": str(document_id),
                        "expected": expected,
                        "actual": actual,
                    },
                )

    async def _report(self, stage: str, progress: int) -> None:
        if self._on_progress is not None:
            await self._on_progress(stage, progress)


def _document_language_manifest_fields(
    document: Document,
    chunks: list[DocumentChunk],
) -> dict[str, object]:
    """Persist per-document language counts so hard-scoped retrieval can skip useless rewrites."""
    chunk_counts: dict[str, int] = {}
    document_language = normalize_routing_language(document.language)
    for chunk in chunks:
        snapshot = build_index_language_snapshot(
            content=chunk.content,
            chunk_metadata=chunk.chunk_metadata,
            document_language=document.language,
        )
        chunk_language = snapshot["chunk_language"]
        chunk_counts[chunk_language] = chunk_counts.get(chunk_language, 0) + 1
        if document_language in {ROUTING_LANGUAGE_MIXED, ROUTING_LANGUAGE_UNKNOWN}:
            document_language = snapshot["document_language"]
    return {
        "document_language": document_language,
        "chunk_language_counts": dict(sorted(chunk_counts.items())),
    }


def _language_inventory(
    chunks: list[tuple[Document, DocumentChunk]],
) -> dict[str, dict[str, int]]:
    chunk_counts: dict[str, int] = {}
    document_counts: dict[str, int] = {}
    seen_documents: set[uuid.UUID] = set()
    for document, chunk in chunks:
        snapshot = build_index_language_snapshot(
            content=chunk.content,
            chunk_metadata=chunk.chunk_metadata,
            document_language=document.language,
        )
        chunk_language = snapshot["chunk_language"]
        chunk_counts[chunk_language] = chunk_counts.get(chunk_language, 0) + 1
        if document.id in seen_documents:
            continue
        seen_documents.add(document.id)
        document_language = normalize_routing_language(document.language)
        if document_language in {ROUTING_LANGUAGE_MIXED, ROUTING_LANGUAGE_UNKNOWN}:
            document_language = snapshot["document_language"]
        document_counts[document_language] = document_counts.get(document_language, 0) + 1
    return {
        "chunk_language_counts": dict(sorted(chunk_counts.items())),
        "document_language_counts": dict(sorted(document_counts.items())),
    }


_READY_AFTER_ACTIVATION = {
    DocumentStatus.CHUNKED,
    DocumentStatus.EMBEDDED,
    DocumentStatus.EMBEDDING,
    DocumentStatus.INDEXING,
    DocumentStatus.READY,
}


def document_ids_from_build_manifest(manifest: object) -> dict[uuid.UUID, int]:
    """Return document_id → version from an IndexBuild manifest."""
    raw = manifest.get("documents") if isinstance(manifest, dict) else None
    if not isinstance(raw, list):
        return {}
    versions: dict[uuid.UUID, int] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            document_id = uuid.UUID(str(item.get("document_id")))
            versions[document_id] = int(item["document_version"])
        except (KeyError, TypeError, ValueError):
            continue
    return versions


async def mark_included_documents_ready(
    session: AsyncSession, project_id: uuid.UUID, build: IndexBuild
) -> None:
    """Documents in an activated snapshot are searchable, so they become ready."""
    expected_versions = document_ids_from_build_manifest(build.manifest)
    if not expected_versions:
        return
    result = await session.execute(
        select(Document).where(
            Document.project_id == project_id,
            Document.id.in_(tuple(expected_versions)),
            Document.deleted_at.is_(None),
        )
    )
    for document in result.scalars():
        if expected_versions.get(document.id) != document.version:
            continue
        if document.status not in _READY_AFTER_ACTIVATION:
            continue
        entries = build.manifest.get("documents", [])
        entry: dict[str, object] = next(
            (item for item in entries if item.get("document_id") == str(document.id)), {}
        )
        generation = entry.get("chunk_generation_id")
        document.chunk_generation_id = uuid.UUID(str(generation)) if generation else None
        document.status = DocumentStatus.READY
        document.error_message = None


async def activate_index_build(
    session: AsyncSession, project_id: uuid.UUID, build: IndexBuild, *, rollback: bool = False
) -> ProjectIndexPointer:
    """Atomically move the one authoritative pointer to a validated build."""
    await verify_structural_build(session, project_id, build)
    if (
        build.project_id != project_id
        or build.state not in {IndexBuildState.VALIDATED, IndexBuildState.RETAINED}
        or build.validated_at is None
        or build.corpus_fingerprint is None
        or build.vector_count != build.chunk_count
        or build.keyword_count != build.chunk_count
    ):
        raise PermanentJobError(
            "Only validated retained builds can be activated.", code="index_build_not_activatable"
        )
    await acquire_project_stage_lock(session, project_id=project_id, stage="index_activation")
    await require_build_acceptance(
        session, project_id, build, **({"rollback": True} if rollback else {})
    )
    await verify_semantic_scope_snapshot(session, project_id, build)
    repository = IndexBuildRepository(session, project_id)
    pointer = await repository.get_pointer(for_update=True)
    if pointer is None:
        pointer = ProjectIndexPointer(project_id=project_id)
        repository.add_pointer(pointer)
        await session.flush()
    if pointer.active_build_id == build.id:
        await mark_included_documents_ready(session, project_id, build)
        await session.flush()
        return pointer
    old_active = (
        await repository.get_by_id(pointer.active_build_id, for_update=True)
        if pointer.active_build_id is not None
        else None
    )
    pointer.previous_build_id = pointer.active_build_id
    pointer.active_build_id = build.id
    if old_active is not None:
        old_active.state = IndexBuildState.RETAINED
    build.state = IndexBuildState.ACTIVE
    build.activated_at = datetime.now(UTC)
    await mark_included_documents_ready(session, project_id, build)
    await session.flush()
    return pointer
