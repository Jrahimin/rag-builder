# Knowledge — Document Ingestion (Phases 1–3)

## Purpose

Project-scoped file upload and ingestion pipeline: store raw bytes, extract text asynchronously, split into chunks for retrieval. **Knowledge v1** completes at `status=chunked` — embeddings and indexing are owned by the `retrieval` module.

## Architecture

```text
Client → documents_router → DocumentService → PostgreSQL (documents, document_chunks)
                          ↘ BaseStorageProvider (raw + parsed text)
                          ↘ JobQueue → Redis/Taskiq → DocumentProcessingWorkflow
                                                    ↘ Parser providers
                                                    ↘ ChunkingService
                          ↘ (on delete) RetrievalCleanupService — PG embeddings + vector purge
```

Upload hashes stream through a spooled temp file (`SpooledTemporaryFile`) so large
files are not fully buffered in memory. Exceeding `APE_KNOWLEDGE__MAX_UPLOAD_BYTES`
raises `413 Payload Too Large`.

## Ingestion lifecycle

| Status | Meaning |
| ------ | ------- |
| `uploaded` | Bytes stored (brief) |
| `queued` | Job enqueued (only status the worker accepts as entry) |
| `parsing` | Worker reading/parsing file — a crash mid-processing leaves this status; recover via `reprocess` |
| `chunking` | Transient marker only — persisted in the same commit as the terminal status |
| `chunked` | **Ingestion complete** for Knowledge v1 |
| `failed` | Safe `error_message` on document |

Owned by the retrieval module: `embedding`, `embedded`, `indexing`, `ready`.

## Configuration

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `APE_STORAGE__BACKEND` | `local` | Object storage backend |
| `APE_JOBS__BACKEND` | `taskiq` | `taskiq` or `inline` (tests) |
| `APE_KNOWLEDGE__MAX_UPLOAD_BYTES` | `52428800` (50 MB) | Upload size limit (413 when exceeded) |
| `APE_CHUNKING__STRATEGY` | `recursive_character` | Chunking strategy (only option today) |
| `APE_CHUNKING__CHUNK_SIZE` | `1000` | Target characters per chunk |
| `APE_CHUNKING__CHUNK_OVERLAP` | `200` | Overlap between chunks |

## API

| Method | Path | Description |
| ------ | ---- | ----------- |
| `POST` | `/documents` | Upload + enqueue processing |
| `GET` | `/documents` | List documents |
| `GET` | `/documents/{id}` | Document metadata |
| `GET` | `/documents/{id}/chunks` | Paginated chunks |
| `POST` | `/documents/{id}/reprocess` | Re-run pipeline (bumps `version`) |
| `DELETE` | `/documents/{id}` | Soft-delete + remove storage + chunks + retrieval artifacts |

## Design decisions

| Decision | Rationale |
| -------- | --------- |
| Worker entry guard (`status=queued` only) | Duplicate or stale job delivery is a no-op instead of re-running parse/chunk |
| `parsing` committed; `chunking` is a stage marker | Crash mid-parse leaves a recoverable state; reprocess re-enqueues from any non-terminal status |
| `RetrievalCleanupService` on delete | Knowledge owns the delete API; retrieval artifacts are purged via a lightweight composition callback — no full `IndexingService` wiring |
| `ChunkingStrategy` config seam | Only `recursive_character` today; enum + factory ready for future strategies without API changes |

## Worker

```bash
python worker.py
# or: taskiq worker app.worker.broker:broker app.worker.handlers.document app.worker.handlers.embedding app.worker.handlers.indexing
```

## Testing

- Unit: `tests/unit/modules/knowledge/` (upload, dedup, size limit, reprocess)
- Integration: `tests/integration/test_documents_api.py` (upload, reprocess version bump, 413 on oversized file)

## Related

- [Retrieval](./retrieval_module.md) — embed → index → search (`chunked` → `ready`)
- [API reference](../api/knowledge_api.md)
- [Plan](../plans/knowledge_module_plan.md)
