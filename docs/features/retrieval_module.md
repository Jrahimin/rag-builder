# Retrieval Module

Project-scoped embedding, PostgreSQL-native semantic retrieval, keyword indexing,
and hybrid search.
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
documents_router (embed/index) ──► IndexingService ──► JobRun + outbox
search_router ──► SearchService ──► RetrievalContext ──► Retriever strategy
                                                    ├── SemanticRetriever
                                                    └── HybridRetriever
                                                          ├── KeywordRetriever (BM25)
                                                          ├── SemanticRetriever
                                                          ├── RRF fusion
                                                          └── RerankerProvider
                                                    └── ResultHydrator (once)
Worker handlers ──► EmbeddingWorkflow / RetrievalIndexingWorkflow + KeywordIndexingWorkflow
                 └──► chunk_embeddings (pgvector) + chunk_keyword_index (PostgreSQL)
```

| Component | Role |
| --------- | ---- |
| **IndexingService** | Business validation plus durable job staging; wired from one Settings snapshot by `composition/retrieval.py` |
| **EmbeddingWorkflow** / **RetrievalIndexingWorkflow** | Stage work only; shared skeleton in `workflows/stage_runner.py` |
| **KeywordIndexingWorkflow** | BM25/FTS rows in `chunk_keyword_index`; invoked during `document.index` |
| **SemanticRetriever** / **KeywordRetriever** | Candidate-only retrievers (`chunk_id`, `score`, `source`) |
| **HybridRetriever** | Concurrent semantic + keyword → RRF → optional rerank |
| **ResultHydrator** | Single hydration point for chunk/document ORM rows |
| **RetrievalCleanupService** | Transactional native-vector, keyword-row, and BM25-stat cleanup |
| **Worker handoff** | Successful process/embed atomically stages an idempotent child job using the parent's immutable configuration snapshot |

## Document lifecycle (retrieval-owned statuses)

| Status | Meaning |
| ------ | ------- |
| `embedding` | Embed job enqueued or worker running `EmbeddingWorkflow` |
| `embedded` | Native vectors persisted in `chunk_embeddings` (PostgreSQL/pgvector) |
| `indexing` | Index job enqueued or worker validating vectors and rebuilding keyword rows |
| `ready` | Native vector and keyword rows are available; document is searchable |

Poll `GET /documents/{id}` until `ready` (or `failed`). Manual triggers: `POST .../embed`, `POST .../index`.
Their responses include `job_id`; [Jobs API](../api/jobs_api.md) exposes execution
progress, attempts, structured failure, and explicit retry.

**Reindex after v2 upgrade:** existing `ready` documents need `POST .../index` once to populate keyword rows.

## Configuration

| Section | Key vars | Role |
| ------- | -------- | ---- |
| `EmbeddingConfig` | `APE_EMBEDDING__*` | Backend (`hash`, `ollama`, `openai`, `gemini`), model, dimensions, API keys |
| `RetrievalConfig` | `APE_RETRIEVAL__*` | `strategy`, candidate pools, `hnsw_ef_search`, RRF weights, reranker, `embedding_set_version`, `filterable_metadata_keys` |

`embedding_set_version` is a deployment-level int, independent of `Document.version`. Bump it after a model change to re-embed; search and index rows filter to the active version.

The production default is hybrid with 40 semantic and 40 keyword candidates before RRF. Search
responses include sanitized strategy, latency, reranker identity, and fallback diagnostics. Phase 4
quality runs persist these values; candidate pools, weights, and reranker promotion should be tuned
from a versioned dataset rather than ad hoc changes.

## Data model

- `chunk_embeddings` — native fixed-dimension `vector(n)` rows with an HNSW cosine index
- `chunk_keyword_index` — normalized text, `search_vector` (GIN), term frequencies, metadata snapshot
- `keyword_term_stats` / `keyword_collection_stats` — BM25 document frequencies and collection stats

Semantic SQL joins `documents` and `document_chunks`, requires a ready,
non-deleted document, and applies `project_id`, active
embedding-set/provider/model, optional document, and allowlisted metadata
filters before ordering candidates by cosine distance.

## Delete policy

`RetrievalCleanupService` deletes pgvector embeddings and keyword rows and
rebuilds affected BM25 statistics in the same transaction as the document
delete. There is no remote purge or eventual-consistency window.

## Workers

```bash
python worker.py
```

## Testing

- Unit: `tests/unit/modules/retrieval/` (retrievers, RRF, hydrator, BM25, config, workflows)
- Integration: `tests/integration/test_retrieval_api.py` (real pgvector ranking,
  semantic + hybrid search, isolation, lifecycle visibility,
  document/version/metadata filters, deletion, and idempotent rebuilds)
- Benchmark: `tests/benchmarks/` (opt-in ingest, index-build p95, search p50/p95, recall@5,
  filtered recall, and hybrid latency)

## Production note

Retrieval v2 ships **hybrid BM25 + vector + RRF + reranker** as the production path (ADR-009). Set `APE_RETRIEVAL__STRATEGY=hybrid` in production. Semantic-only rollback remains via `strategy=semantic` on the request or deployment config.

Chat integrates through `RetrievalPort` without module coupling (ADR-008).

## Related

- [Knowledge](./knowledge_module.md) — ingestion through `chunked`
- [API reference](../api/retrieval_api.md)
- [ADR-007](../architecture/adr/007-staged-retrieval-delivery.md)
- [ADR-009](../architecture/adr/009-retrieval-v2-hybrid-search.md)
- [Hybrid retrieval journey](../learning/hybrid-retrieval-journey.md)
