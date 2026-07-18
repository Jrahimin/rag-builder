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
modules/retrieval/     full immutable build → activate → search     chunked → ready
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
Worker handlers ──► IndexBuildWorkflow
                 ├──► build-scoped pgvector + keyword + BM25 rows
                 └──► validate ──► atomic ProjectIndexPointer swap
```

| Component | Role |
| --------- | ---- |
| **IndexingService** | Business validation plus durable job staging; wired from one Settings snapshot by `composition/retrieval.py` |
| **IndexBuildWorkflow** | Writes a complete private vector+keyword snapshot, validates it, and optionally activates it |
| **IndexLifecycleService** | Durable corpus build staging plus guarded activation/rollback |
| **SemanticRetriever** / **KeywordRetriever** | Candidate-only retrievers (`chunk_id`, `score`, `source`) |
| **HybridRetriever** | Concurrent semantic + keyword → RRF → optional rerank |
| **ResultHydrator** | Single hydration point for chunk/document ORM rows |
| **RetrievalCleanupService** | Irreversible purge cleanup across retained builds |
| **Worker handoff** | Successful process/embed atomically stages an idempotent child job using the parent's immutable configuration snapshot |

## Document lifecycle (retrieval-owned statuses)

| Status | Meaning |
| ------ | ------- |
| `embedding` | A document-triggered full build is queued or running |
| `embedded` | Legacy intermediate accepted by manual index staging; new builds publish vectors and keywords together |
| `indexing` | An isolated full build is queued/running |
| `ready` | The document version is present in an active complete build |
| `deleting` / `purging` | A guarded destructive lifecycle job is pending |

Poll `GET /documents/{id}` until `ready` (or `failed`). Manual triggers: `POST .../embed`, `POST .../index`.
Their responses include `job_id`; [Jobs API](../api/jobs_api.md) exposes execution
progress, attempts, structured failure, and explicit retry.

For whole-corpus changes use `/index-builds/reembed` or `/index-builds/reindex`,
then activate the validated build. The prior active build remains the rollback target.

## Configuration

| Section | Key vars | Role |
| ------- | -------- | ---- |
| `EmbeddingConfig` | `APE_EMBEDDING__*` | Backend (`hash`, `ollama`, `openai`, `gemini`), model, dimensions, API keys |
| `RetrievalConfig` | `APE_RETRIEVAL__*` | `strategy`, candidate pools, `hnsw_ef_search`, RRF weights, reranker, `embedding_set_version`, `filterable_metadata_keys` |

`embedding_set_version` is a deployment-level int, independent of
`Document.version`. Both are captured in a build manifest; search filters by the
active `index_build_id`, which is stricter than selecting the newest embedding set.

The production default is hybrid with 40 semantic and 40 keyword candidates before RRF. Search
responses include sanitized strategy, latency, reranker identity, and fallback diagnostics. Phase 4
quality runs persist these values; candidate pools, weights, and reranker promotion should be tuned
from a versioned dataset rather than ad hoc changes.

## Data model

- `chunk_embeddings` — native fixed-dimension `vector(n)` rows with an HNSW cosine index
- `chunk_keyword_index` — normalized text, `search_vector` (GIN), term frequencies, metadata snapshot
- `keyword_term_stats` / `keyword_collection_stats` — BM25 document frequencies and collection stats
- `index_builds` / `project_index_pointers` — immutable snapshot metadata and atomic active/previous authority

Semantic and keyword SQL apply `project_id`, the resolved active
`index_build_id`, provider/model configuration, optional document, and allowlisted
metadata filters before ranking. A partially written build has no query path.

## Delete policy

Delete first activates a complete snapshot excluding the document, then
soft-deletes it while retaining the previous build and artifacts for rollback.
Purge performs the same safe activation, then removes every relational,
vector/keyword, raw, and parsed artifact and invalidates builds that referenced it.

## Workers

```bash
python worker.py
```

## Testing

- Unit: `tests/unit/modules/retrieval/` (retrievers, RRF, hydrator, BM25, config, lifecycle service)
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
