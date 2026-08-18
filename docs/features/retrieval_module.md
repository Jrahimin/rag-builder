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
| **SemanticRetriever** / **KeywordRetriever** | Candidate-only retrievers (`chunk_id`, ranking `score`, calibrated `semantic_score`, `source`) |
| **HybridRetriever** | Original dense + original lexical, optional one translated pair, RRF provenance, optional rerank |
| **SourceMetadataReadPort** | Composition seam for Knowledge's canonical source generation/applicability scope |
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

Poll `GET /documents/{id}` until `ready` (or `failed`). Manual trigger:
`POST .../embed` (vectors and keywords in one snapshot; auto-activates).
`POST .../index` remains a compatibility alias for the same full build.
Responses include `job_id`; [Jobs API](../api/jobs_api.md) exposes execution
progress, attempts, structured failure, and explicit retry.

For whole-corpus changes use `/index-builds/reembed` (Rebuild index), then
activate the validated build. Activation marks included documents `ready`.
`/index-builds/reindex` is the same snapshot job under a different label.
The prior active build remains the rollback target.

## Configuration

| Section | Key vars | Role |
| ------- | -------- | ---- |
| `EmbeddingConfig` | `APE_EMBEDDING__*` | Backend (`hash`, `ollama`, `openai`, `gemini`), model, dimensions, API keys |
| `RetrievalConfig` | `APE_RETRIEVAL__*` | `strategy`, candidate pools, `hnsw_ef_search`, RRF weights, reranker windows, diversity caps, optional passage scoring, `embedding_set_version`, language-metadata schema |
| `QueryTranslationConfig` | `APE_QUERY_TRANSLATION__*` | Query-only translation; default off; model `gpt-5-nano` |
| `RerankerProviderConfig` | `APE_RERANKER__*` | Cohere credentials used only when `reranker_backend=cohere` |
| `AIConfigPolicy` | `APE_AI_POLICY__SOURCE_POLICY_DEPLOYMENT_CAP` | Emergency maximum for Project `off / observe / enforce` source policy |

`embedding_set_version` is a deployment-level int, independent of
`Document.version`. Both are captured in a build manifest; search filters by the
active `index_build_id`, which is stricter than selecting the newest embedding set.

The production default is hybrid with 50 semantic and 50 keyword candidates before RRF. The rerank
stage stays enabled with the `noop` occupant so fused RRF order is the default ranking and the
provider seam remains in the path. Pass-through skips chunk-text loading and reports
`rerank_status=passthrough`. Query translation stays off until gazette hard gates pass. A Cohere
multilingual reranker may replace the noop occupant only after a stored comparison. Search responses
keep the final ranking `score` separate from `semantic_score` (`1 - cosine_distance`). When rerank
is applied, calibrated `rerank_relevance_score` is the evidence signal; otherwise only
`semantic_score` may act as evidence confidence. Diagnostics declare the reranker score scale rather
than implying that RRF or heuristic scores are probabilities.

Document and section caps are soft diversity preferences. Exact normalized-content deduplication
remains hard; deferred unique chunks backfill in original rank order when the first pass would
underfill `top_k`. Diagnostics distinguish deferred, backfilled, and finally removed candidates.

Optional bounded-passage scoring (`APE_RETRIEVAL__PASSAGE_SCORING_ENABLED`) embeds overlapping,
minimum-sized token windows on the fused candidate window. It records raw cosine as
`passage_semantic_score`, winning offsets, and `bounded_token_max_v1`; it never overwrites
whole-chunk `semantic_score` or ranking `score`. It is disabled by default and may be promoted only
after positive/hard-negative calibration and latency gates pass.

The multilingual dense baseline is OpenAI `text-embedding-3-large` truncated to 1024 dimensions.
Changing model or dimensions requires a new embedding set and complete immutable index build;
dimension changes additionally require an Alembic migration because pgvector columns are fixed-size.
The `private_ollama` profile must use a 1024-dimension embedder against this column;
`nomic-embed-text` at 768 is incompatible with the live `vector(1024)` contract.

## Data model

- `chunk_embeddings` — native fixed-dimension `vector(n)` rows with an HNSW cosine index
- `chunk_keyword_index` — normalized text, `search_vector` (GIN), term frequencies, metadata snapshot
- `keyword_term_stats` / `keyword_collection_stats` — BM25 document frequencies and collection stats
- `index_builds` / `project_index_pointers` — immutable snapshot metadata and atomic active/previous authority

Semantic and keyword SQL apply `project_id`, the resolved active
`index_build_id`, provider/model configuration, optional document, and allowlisted
metadata filters before ranking. A partially written build has no query path.

Source metadata is deliberately not copied into either content index. At search start the service
captures the active index build and Project source generation, then joins the same Knowledge-owned
selectable in both vector and keyword repositories. This lets metadata activation affect the next
request without OCR, chunking, embedding, or index construction. Exact index/config/source IDs are
returned in diagnostics and result metadata for prompts, citations, messages, jobs, and evaluations.

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

Retrieval v2 ships **hybrid BM25 + vector + RRF** as the production path (ADR-009, ADR-018).
Original dense and original lexical always run. One query-only translation pair is optional and
off by default. When translation is applied, search and chat diagnostics include status, source
and target language, provider/model, the translated query, executed branches, and per-candidate
RRF provenance (`original_dense`, `translated_dense:<lang>`, translated lexical). Translated text
is a retrieval artifact: it is not used as grounding confidence, citations, or evidence. The
reranker seam remains a pass-through `noop` occupant until Cohere
`rerank-v4.0-fast` passes gazette hard gates. Set `APE_RETRIEVAL__STRATEGY=hybrid` in production.
Semantic-only rollback remains via `strategy=semantic` on the request or deployment config.

Chat integrates through `RetrievalPort` without module coupling (ADR-008).

## Related

- [Knowledge](./knowledge_module.md) — ingestion through `chunked`
- [API reference](../api/retrieval_api.md)
- [ADR-007](../architecture/adr/007-staged-retrieval-delivery.md)
- [ADR-009](../architecture/adr/009-retrieval-v2-hybrid-search.md)
- [Hybrid retrieval journey](../learning/hybrid-retrieval-journey.md)
