# Knowledge — Document Ingestion (Phases 1–3)

## Purpose

Project-scoped file upload and ingestion pipeline: store raw bytes, extract text asynchronously, split into chunks for future retrieval. **Phase 3** completes ingestion at `status=chunked` — ready for the `retrieval` module (embeddings/indexing are out of scope).

## Architecture

```text
Client → documents_router → DocumentService → PostgreSQL (documents, document_chunks)
                          ↘ BaseStorageProvider (raw + parsed text)
                          ↘ JobQueue → Redis/Taskiq → DocumentProcessingWorkflow
                                                    ↘ Parser providers
                                                    ↘ ChunkingService
```

## Ingestion lifecycle

| Status | Meaning |
| ------ | ------- |
| `uploaded` | Bytes stored (brief) |
| `queued` | Job enqueued |
| `parsing` | Worker reading/parsing file |
| `chunking` | Splitting parsed text |
| `chunked` | **Ingestion complete** for Knowledge v1 |
| `failed` | Safe `error_message` on document |

Reserved for future modules: `embedding`, `indexing`, `ready`.

## Configuration

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `APE_STORAGE__BACKEND` | `local` | Object storage backend |
| `APE_JOBS__BACKEND` | `taskiq` | `taskiq` or `inline` (tests) |
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
| `DELETE` | `/documents/{id}` | Soft-delete + remove storage + chunks |

## Worker

```bash
taskiq worker app.worker.broker:broker app.worker.handlers.document
# or: python worker.py
```

## Testing

- Unit: `tests/unit/modules/knowledge/`
- Integration: `tests/integration/test_documents_api.py`

## Future

Embeddings and vector indexing in the `retrieval` module; transitions `chunked` → `ready`.
