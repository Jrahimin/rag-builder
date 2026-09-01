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
| **HybridRetriever** | Original dense + original lexical, optional one translated pair, bounded incoming `MODIFIES` recall, RRF provenance, optional rerank |
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

## Operator workflow: configuration → build → activation → retrieval → diagnostics → rollback

Query embeddings always follow the **active** index build
(`embedding_set_version` + provider + model + dimensions). Live
`APE_EMBEDDING__*` settings are the **target** for the next rebuild only.

1. **Configure the target.** Set embedding backend/model/dimensions and bump
   `APE_RETRIEVAL__EMBEDDING_SET_VERSION`. Keep the previous provider credentials
   until rollback is no longer needed. Project AI policy may override translation
   and `rerank_mode`; it does not own embedding identity.
2. **Build.** `POST .../index-builds/reembed` writes a private snapshot with the
   target embedder. Job configuration captures that target so a later worker
   cannot mix spaces. Validate must pass (`vector_count == chunk_count`).
3. **Activate.** `POST .../index-builds/{id}/activate` atomically swaps
   `ProjectIndexPointer`. Search, chat grounding, and message `embedding_set_version`
   now use the new identity.
4. **Retrieve and inspect diagnostics.** Search/chat diagnostics report
   `embedding_identity_status=matched` plus provider/model/dimensions/esv,
   translation status, and rerank status. Translation or reranker failure degrades
   (original-query retrieval; RRF + cosine; `rerank_status=unavailable`). Embedding
   incompatibility does **not** degrade: unlabeled, mixed, or unmatched identity
   returns `409 embedding_identity_unlabeled` / `embedding_identity_incompatible`
   or `503 embedding_provider_unavailable`.
5. **Roll back** with `POST .../index-builds/rollback`. The retained previous
   build keeps its own identity; query embeddings switch back to that space.
   Rollback refuses with `400 index_rollback_provider_unavailable` if that
   historical provider key is gone, instead of moving the pointer and leaving
   the next search `503`. Unlabeled or mixed retained identity is also refused.

OpenAI → Cohere: set Cohere as the live target and bump esv to 3, rebuild,
validate, activate. Until activation, queries still use the OpenAI active
build. After rollback, they use OpenAI again. Do not delete the OpenAI key
while a retained OpenAI build is a rollback target.

Evidence fallback and claim-grounding thresholds stay provider-specific
calibration. `hosted_managed` examples now use `APE_CHAT__EVIDENCE_GATE_MODE=enforce`
with the current defaults (semantic `0.35`, applied reranker `0.40`, lexical
`0.30`/`0.50`, claim `0.35`/`0.25`/`0.15`). Recalibrate those numbers in Test Lab
if a corpus disagrees; keep `observe` only while measuring.

## Configuration

| Section | Key vars | Role |
| ------- | -------- | ---- |
| `EmbeddingConfig` | `APE_EMBEDDING__*` | Target backend (`hash`, `ollama`, `openai`, `gemini`, `cohere`), model, dimensions, API keys. Query search uses the active build identity, not this live target. |
| `RetrievalConfig` | `APE_RETRIEVAL__*` | `strategy`, candidate pools, `hnsw_ef_search`, RRF weights, reranker windows, diversity caps, optional passage scoring, bounded modifier expansion, `embedding_set_version`, language-metadata schema |
| `QueryTranslationConfig` | `APE_QUERY_TRANSLATION__*` | Query-only translation; default off; model `gpt-5-nano` |
| `CohereConfig` | `APE_COHERE__*` | Shared credential and base URL for embed and rerank |
| `RerankerProviderConfig` | `APE_RERANKER__*` | Rerank model/timeout (default 10s); missing key or API failure degrades to RRF + cosine and records a sanitized `rerank_failure_reason` |
| `AIConfigPolicy` | `APE_AI_POLICY__SOURCE_POLICY_MODE` | Deployment default for Project `off / observe / enforce` source policy (`off`). Inherited when a revision omits the leaf |
| `AIConfigPolicy` | `APE_AI_POLICY__SOURCE_POLICY_DEPLOYMENT_CAP` | Emergency maximum for Project/global source policy. Restricts only; never activates a higher mode |

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
whole-chunk `semantic_score` or ranking `score`. It stays off by default for evaluation and
debugging. Production grounding instead runs an adaptive rescue: after normal candidate
assessment, at most four high-confidence reranker near-misses without a passage score are
passage-scored and reassessed. Promote always-on retrieval scoring only after
positive/hard-negative calibration and latency gates pass.

The multilingual dense baseline for `hosted_managed` is Cohere `embed-v4.0` at 1024 dimensions
(`embedding_set_version=3`) with `QUERY`/`DOCUMENT` purpose. `hosted_openai` compatibility keeps
OpenAI `text-embedding-3-large` at 1024 (`embedding_set_version=2`). Changing model or dimensions
requires a new embedding set and complete immutable index build; dimension changes additionally
require an Alembic migration because pgvector columns are fixed-size.
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

Optional current-authority expansion is off by default. `APE_RETRIEVAL__MODIFIES_EXPANSION_MODE`
(`off` / `observe` / `expand`) wins when set; the legacy boolean
`APE_RETRIEVAL__MODIFIES_EXPANSION_ENABLED=true` still means expand. `observe` reports
eligible and excluded incoming `MODIFIES` relationships without changing recall. `expand`
searches bounded depth-one incoming edges whose target revision was retrieved. Knowledge
resolves every edge against the same Project, source generation, `as_of`, and active index build and
returns one fail-closed inclusion/exclusion outcome. A stale or replaced modifier revision is
reported as `stale_or_replaced_revision`, not as a cross-project miss. At most eight modifier
documents and twenty relationship-origin chunks are admitted. Related branches reuse the original
multilingual query variants, are fused with the base candidates, and share the single existing
reranker call. Relationship metadata is retained as recall provenance but is never a grounding
signal. Public `document_id` search remains a hard single-document scope: expansion does not add
modifier documents under that contract. Chat can still expand when the request is unscoped.
Existing post-rerank source-policy consolidation remains in place; retrieval diagnostics separately
report modifier exclusions, candidates retrieved, reranked, later removals by reason, and unfilled
result slots. Grounding assessments apply only to candidates presented to grounding; earlier
policy/hydration/dedup removals do not require those assessments.

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
Original dense and original lexical always run. One query-only translation pair is optional;
`hosted_managed` enables it with domain-neutral `retrieval-translation-v2`. When translation is
applied, search and chat diagnostics include status, source and target language, provider/model,
latency, executed branches, and per-candidate RRF provenance. Translated query text is a retrieval
artifact kept on internal chat/evaluation hits for grounding. Public `RetrievalResult.query_variants`
omit that text when translated-query persistence is disabled. It is not used as grounding
confidence, citations, or evidence.
`hosted_managed` reranks with Cohere `rerank-v4.0-pro` (platform default Always). Missing rerank
credentials or API failure preserve fused RRF order (`rerank_status=unavailable`) and fall back
to cosine evidence. Set `APE_RETRIEVAL__STRATEGY=hybrid` in production.
Semantic-only rollback remains via `strategy=semantic` on the request or deployment config.

Chat integrates through `RetrievalPort` without module coupling (ADR-008).

## Related

- [Knowledge](./knowledge_module.md) — ingestion through `chunked`
- [API reference](../api/retrieval_api.md)
- [ADR-007](../architecture/adr/007-staged-retrieval-delivery.md)
- [ADR-009](../architecture/adr/009-retrieval-v2-hybrid-search.md)
- [Hybrid retrieval journey](../learning/hybrid-retrieval-journey.md)
