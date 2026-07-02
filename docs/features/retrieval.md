# Retrieval Module

Project-scoped embedding, vector indexing, and semantic search. Extends the knowledge pipeline from `chunked` through `ready`, and exposes the search API.

## Purpose

Turn parsed document chunks into searchable vectors while keeping knowledge ingestion and retrieval concerns separate (ADR-007).

## Architecture

```text
modules/knowledge/     upload → parse → chunk     status=chunked
modules/retrieval/     embed → index → search      chunked → ready → POST /search
```

```text
documents_router (embed/index) ──► IndexingService ──► JobQueue
search_router ──► SearchService ──► SemanticRetriever ──► BaseEmbeddingProvider
                                                    └──► BaseVectorStoreProvider
Worker handlers ──► EmbeddingWorkflow / VectorIndexingWorkflow (stage_runner)
                 └──► chunk_embeddings (PostgreSQL) + Qdrant points
```

| Component | Role |
| --------- | ---- |
| **IndexingService** | Status validation, job enqueue (built via `IndexingService.from_settings`) |
| **EmbeddingWorkflow** / **VectorIndexingWorkflow** | Stage work only; shared skeleton in `workflows/stage_runner.py` |
| **SemanticRetriever** | Query embedding + vector search + chunk hydration; results filtered to active `embedding_set_version` |
| **RetrievalCleanupService** | Lightweight delete cascade (PG embeddings + best-effort vector purge) used by the knowledge delete path |
| **Worker handoff** | After `document.process` reaches `chunked`, worker calls `IndexingService.enqueue_embed_if_enabled` |

## Document lifecycle (retrieval-owned statuses)

| Status | Meaning |
| ------ | ------- |
| `embedding` | Embed job enqueued or worker running `EmbeddingWorkflow` |
| `embedded` | Vectors persisted in `chunk_embeddings` (PostgreSQL) |
| `indexing` | Index job enqueued or worker running `VectorIndexingWorkflow` |
| `ready` | Points in vector store; document is searchable |

Poll `GET /documents/{id}` until `ready` (or `failed`). Manual triggers: `POST .../embed`, `POST .../index`.

## Configuration

| Section | Key vars | Role |
| ------- | -------- | ---- |
| `EmbeddingConfig` | `APE_EMBEDDING__*` | Backend (`hash`, `ollama`, `openai`, `gemini`), model, dimensions, API keys |
| `VectorStoreConfig` | `APE_VECTOR_STORE__*` | Qdrant collection name |
| `RetrievalConfig` | `APE_RETRIEVAL__*` | `auto_embed`, `auto_index`, `default_top_k`, `score_threshold`, `embedding_set_version`, `filterable_metadata_keys` |

`embedding_set_version` is a deployment-level int, independent of `Document.version`. Bump it after a model change to re-embed; search and Qdrant payloads filter to the active version so stale vectors are excluded.

## Data model

`chunk_embeddings` stores packed float32 vectors (`BYTEA`) with metadata and unique key `(chunk_id, embedding_set_version, provider, model)`.

Qdrant payload includes `project_id`, `document_id`, `chunk_index`, `embedding_set_version`, plus allowlisted chunk metadata keys.

## Delete policy

On document soft-delete: remove PG embeddings + chunks, best-effort Qdrant purge via `RetrievalCleanupService` (wired in `dependencies/knowledge.py` — composition layer only).

## Workers

```bash
python worker.py
```

`worker.py` registers document, embedding, and indexing handlers in one process.

Job retries: `RetryPolicy` on each `JobDefinition` is translated to Taskiq `SmartRetryMiddleware` labels at dispatch (`platform/jobs/registry.py`).

## Testing

- Unit: `tests/unit/modules/retrieval/` (`IndexingService`, `SemanticRetriever`, workflows, job registry)
- Integration: `tests/integration/test_retrieval_api.py` (search, metadata filter, auto embed→index chain)

## Production note

Phase 1 ships **semantic retrieval baseline** only. Hybrid BM25 + RRF + reranker is **Retrieval v2** (ADR-007) — required before Chat in production RAG paths.

## Related

- [Knowledge](./knowledge.md) — ingestion through `chunked`
- [Plan](../plans/retrieval-module.md)
- [API reference](../api/retrieval.md)
- [ADR-007](../architecture/adr/007-staged-retrieval-delivery.md)
