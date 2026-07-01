# Knowledge Module — Implementation Plan

Three gated phases: **Phase 1** (upload + storage), **Phase 2** (async parsing), **Phase 3** (chunking). Each phase ships code, tests, docs, and a manual gate checklist before the next phase starts.

## Layout

| Concern | Location |
| ------- | -------- |
| Module | `backend/app/modules/knowledge/` |
| Routes | `backend/app/api/v1/routes/documents_router.py` |
| DI | `backend/app/dependencies/knowledge.py` |
| ORM | `backend/app/models/document.py` |

**API prefix:** `/api/v1/projects/{project_id}/documents`

## Phase 1 — Shipped scope

- `StorageConfig` + `BaseStorageProvider` (local filesystem, MinIO via boto3)
- `Document` model + migration `0004_add_documents`
- Upload, list, get, soft-delete (storage object removed on delete)
- Duplicate `content_sha256` per project → `409`

## Phase 2 — Shipped scope

- `TaskiqJobQueue` + `worker` Docker service + `document.process` job
- `DocumentProcessingWorkflow` (parse-only, stops at `parsed`)
- `CompositeDocumentParserProvider` (plain text + PyMuPDF)
- Upload enqueues parsing; `POST .../reprocess`; processing columns migration `0005`

## Phase 3 — Shipped scope

- `ChunkingConfig` (`APE_CHUNKING__CHUNK_SIZE`, `APE_CHUNKING__CHUNK_OVERLAP`)
- `DocumentChunk` model + migration `0006_add_document_chunks`
- `DocumentChunkRepository` + `ChunkingService` (langchain `RecursiveCharacterTextSplitter`)
- Workflow ends at `status=chunked`; `GET /documents/{id}/chunks`

Knowledge module v1 complete at `chunked` — embeddings/indexing deferred to `retrieval`.

See the Cursor plan file for full gate checklists and architectural decisions.
