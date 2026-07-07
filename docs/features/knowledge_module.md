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
| `APE_CHUNKING__STRATEGY` | `auto` | Chunking strategy (`auto`, `markdown`, `heading`, `structure`, `semantic`, `recursive_fallback`) |
| `APE_CHUNKING__TARGET_TOKENS` | `250` | Approximate target tokens per chunk |
| `APE_CHUNKING__MAX_TOKENS` | `400` | Approximate maximum tokens per chunk |
| `APE_CHUNKING__MIN_TOKENS` | `50` | Minimum tokens before adjacent merge |
| `APE_CHUNKING__OVERLAP_TOKENS` | `50` | Approximate overlap for recursive fallback splits |
| `APE_CHUNKING__STRUCTURE_SCORE_THRESHOLD` | `0.55` | Minimum structure score for structure-first chunking |
| `APE_CHUNKING__SIMILARITY_DROP_THRESHOLD` | `0.35` | Semantic boundary threshold for weakly structured docs |
| `APE_CHUNKING__TOKEN_COUNT_METHOD` | `unicode_property_v1` | Unicode-property token counting |
| `APE_OCR__ENABLED` | `false` | Enable OCR for image uploads and scanned PDF pages |
| `APE_OCR__LANG` | `en` | Deployment-default PaddleOCR language; overridable per document via `ocr_lang` on upload/reprocess |
| `APE_PARSING__PDF_TEXT_PARSERS` | `pymupdf,pdfium` | PDF text parser order before OCR fallback |
| `APE_PARSING__MIN_PAGE_QUALITY_SCORE` | `0.55` | Minimum Unicode parse quality score to accept a page |
| `APE_PARSING__MIN_DOCUMENT_SUCCESS_RATIO` | `0.2` | Minimum accepted-page ratio before failing a document |

See [multilingual_support.md](multilingual_support.md) for Bangla/multilingual processing and OCR language overrides.

### Known limitation: Bangla OCR

The ingestion pipeline (parse quality gate → PDFium → PaddleOCR fallback) is implemented, but **Bangla OCR is not production-ready in Phase 1**. PaddleOCR 3.7 has no stock `bn` models; English OCR on Bangla pages produces unreliable text. Unicode Bengali in the PDF text layer or non-PDF uploads still ingest correctly. Details: [multilingual_support.md — Bangla OCR limitation](multilingual_support.md#known-limitation-bangla-bengali-ocr).

## API

| Method | Path | Description |
| ------ | ---- | ----------- |
| `POST` | `/documents` | Upload + enqueue processing (optional `ocr_lang` form field) |
| `GET` | `/documents` | List documents |
| `GET` | `/documents/{id}` | Document metadata |
| `GET` | `/documents/{id}/chunks` | Paginated chunks |
| `POST` | `/documents/{id}/reprocess` | Re-run pipeline (bumps `version`; optional `ocr_lang` query) |
| `DELETE` | `/documents/{id}` | Soft-delete + remove raw/parsed storage + chunks + retrieval artifacts |

## Design decisions

| Decision | Rationale |
| -------- | --------- |
| Worker entry guard (`status=queued` only) | Duplicate or stale job delivery is a no-op instead of re-running parse/chunk |
| `parsing` committed; `chunking` is a stage marker | Crash mid-parse leaves a recoverable state; reprocess re-enqueues from any non-terminal status |
| `RetrievalCleanupService` on delete | Knowledge owns the delete API; retrieval artifacts are purged via a lightweight composition callback — no full `IndexingService` wiring |
| `ChunkingStrategy` config seam | Auto-selects markdown/heading/structure/semantic strategies from parser elements and structure signals; recursive fallback for oversized sections |

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
