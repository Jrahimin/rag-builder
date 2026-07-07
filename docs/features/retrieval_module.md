# Retrieval Module

Project-scoped embedding, vector indexing, keyword indexing, and hybrid search.
Extends the knowledge pipeline from `chunked` through `ready`, and exposes the
search API.

## Purpose

Turn parsed document chunks into searchable vectors and keyword index rows while
keeping knowledge ingestion and retrieval concerns separate (ADR-007, ADR-009).

## Architecture

```text
modules/knowledge/     upload → parse → chunk     status=chunked
modules/retrieval/     embed → index → search      chunked → ready → POST /search
```

```text
documents_router (embed/index) ──► IndexingService ──► JobQueue
search_router ──► SearchService ──► RetrievalContext ──► Retriever strategy
                                                    ├── SemanticRetriever
                                                    └── HybridRetriever
                                                          ├── KeywordRetriever (BM25)
                                                          ├── SemanticRetriever
                                                          ├── RRF fusion
                                                          └── RerankerProvider
                                                    └── ResultHydrator (once)
Worker handlers ──► EmbeddingWorkflow / VectorIndexingWorkflow + KeywordIndexingWorkflow
                 └──► chunk_embeddings + chunk_keyword_index (PostgreSQL) + Qdrant points
```

| Component | Role |
| --------- | ---- |
| **IndexingService** | Status validation, job enqueue (built via `IndexingService.from_settings`) |
| **EmbeddingWorkflow** / **VectorIndexingWorkflow** | Stage work only; shared skeleton in `workflows/stage_runner.py` |
| **KeywordIndexingWorkflow** | BM25/FTS rows in `chunk_keyword_index`; invoked during `document.index` |
| **SemanticRetriever** / **KeywordRetriever** | Candidate-only retrievers (`chunk_id`, `score`, `source`) |
| **HybridRetriever** | Concurrent semantic + keyword → RRF → optional rerank |
| **ResultHydrator** | Single hydration point for chunk/document ORM rows |
| **RetrievalCleanupService** | PG embeddings + keyword rows + best-effort vector purge on delete |
| **Worker handoff** | After `document.process` reaches `chunked`, worker calls `IndexingService.enqueue_embed_if_enabled` |

## Document lifecycle (retrieval-owned statuses)

| Status | Meaning |
| ------ | ------- |
| `embedding` | Embed job enqueued or worker running `EmbeddingWorkflow` |
| `embedded` | Vectors persisted in `chunk_embeddings` (PostgreSQL) |
| `indexing` | Index job enqueued or worker running vector + keyword indexing |
| `ready` | Vector points and keyword rows indexed; document is searchable |

Poll `GET /documents/{id}` until `ready` (or `failed`). Manual triggers: `POST .../embed`, `POST .../index`.

**Reindex after v2 upgrade:** existing `ready` documents need `POST .../index` once to populate keyword rows.

## Configuration

| Section | Key vars | Role |
| ------- | -------- | ---- |
| `EmbeddingConfig` | `APE_EMBEDDING__*` | Backend (`hash`, `ollama`, `openai`, `gemini`), model, dimensions, API keys |
| `VectorStoreConfig` | `APE_VECTOR_STORE__*` | Qdrant collection name |
| `RetrievalConfig` | `APE_RETRIEVAL__*` | `strategy`, candidate pools, RRF weights, reranker, `embedding_set_version`, `filterable_metadata_keys` |

`embedding_set_version` is a deployment-level int, independent of `Document.version`. Bump it after a model change to re-embed; search and index rows filter to the active version.

## Data model

- `chunk_embeddings` — packed float32 vectors (`BYTEA`)
- `chunk_keyword_index` — normalized text, `search_vector` (GIN), term frequencies, metadata snapshot
- `keyword_term_stats` / `keyword_collection_stats` — BM25 document frequencies and collection stats

Qdrant payload includes `project_id`, `document_id`, `chunk_index`, `embedding_set_version`, plus allowlisted chunk metadata keys.

## Delete policy

On document soft-delete: remove PG embeddings + keyword rows + chunks, best-effort Qdrant purge via `RetrievalCleanupService` (wired in `dependencies/knowledge.py`).

## Workers

```bash
python worker.py
```

## Testing

- Unit: `tests/unit/modules/retrieval/` (retrievers, RRF, hydrator, BM25, config, workflows)
- Integration: `tests/integration/test_retrieval_api.py` (semantic + hybrid search, isolation, metadata filters)

## Production note

Retrieval v2 ships **hybrid BM25 + vector + RRF + reranker** as the production path (ADR-009). Set `APE_RETRIEVAL__STRATEGY=hybrid` in production. Semantic-only rollback remains via `strategy=semantic` on the request or deployment config.

Chat integrates through `RetrievalPort` without module coupling (ADR-008).

## Related

- [Knowledge](./knowledge_module.md) — ingestion through `chunked`
- [API reference](../api/retrieval_api.md)
- [ADR-007](../architecture/adr/007-staged-retrieval-delivery.md)
- [ADR-009](../architecture/adr/009-retrieval-v2-hybrid-search.md)
- [Hybrid retrieval journey](../learning/hybrid-retrieval-journey.md)
