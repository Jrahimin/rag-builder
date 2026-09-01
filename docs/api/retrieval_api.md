# Retrieval API

Hybrid and semantic search plus indexing endpoints. Requires documents at `status=ready`.

**Prefix:** `/api/v1/projects/{project_id}`

> `/documents/{document_id}/embed` and `/documents/{document_id}/index` are
> mounted on the documents router for URL consistency but are owned by the
> **retrieval** module.

Search results are filtered to the deployment's active `embedding_set_version`
(`APE_RETRIEVAL__EMBEDDING_SET_VERSION`) so vectors and keyword rows from prior
embedding runs are excluded.

Embedding persists native pgvector rows. Indexing (`POST .../index`) refreshes
the PostgreSQL keyword index while retaining the existing lifecycle contract.

## POST `/documents/{document_id}/embed`

Enqueue embedding for a `chunked` document.

**202 response:** existing Document shape with `data.status=embedding` and additive
`data.job_id`; inspect the run through the [Jobs API](./jobs_api.md).

## POST `/documents/{document_id}/index`

Enqueue retrieval-index finalization for an `embedded` document. The worker
validates the current native embedding set, rebuilds keyword/BM25 rows, and
marks the document ready in one PostgreSQL transaction.

**202 response:** existing Document shape with `data.status=indexing` and additive
`data.job_id`; the worker eventually moves the Document to `ready`.

## POST `/search`

Search over indexed chunks using the deployment strategy (`semantic` or `hybrid`). The production
default is `hybrid`; semantic remains an explicit comparison/rollback strategy.

**Request:**

```json
{
  "query": "What is the refund policy?",
  "top_k": 5,
  "document_id": null,
  "metadata_filter": { "source": "handbook" },
  "strategy": "hybrid",
  "as_of": "2026-08-16T00:00:00Z"
}
```

| Field | Required | Notes |
| ----- | -------- | ----- |
| `query` | yes | 1–32000 characters |
| `top_k` | no | Default from `APE_RETRIEVAL__DEFAULT_TOP_K` |
| `document_id` | no | Hard single-document scope; current-authority expansion does not add modifier documents |
| `metadata_filter` | no | Allowlisted keys only; others stripped |
| `strategy` | no | `semantic` or `hybrid`; default from config |
| `as_of` | no | Explicit historical selector; source intervals are evaluated at this date without natural-language inference |
| `rerank` | deprecated | Observed in compatibility mode; rejected in strict mode |

`top_k` is bounded by `APE_AI_POLICY__MAX_REQUEST_TOP_K`, and `strategy` must be in
`APE_AI_POLICY__ENABLED_RETRIEVAL_STRATEGIES`. Project policy supplies reranking and other ranking
thresholds. Deprecated `rerank` use appears in `diagnostics.compatibility_diagnostics`; strict mode
returns `request_policy_override_forbidden`.

**Score semantics:** `score` is used only to order results: semantic-only returns
`1 - cosine_distance`, while hybrid returns the final RRF or reranker score.
`semantic_score` is always calibrated as `1 - cosine_distance` against the active
build and is the only score allowed to drive evidence sufficiency. RRF and reranker
scores are not confidence probabilities.

**Response:**

```json
{
  "success": true,
  "data": {
    "query": "What is the refund policy?",
    "top_k": 5,
    "results": [
      {
        "chunk_id": "…",
        "document_id": "…",
        "chunk_index": 0,
        "content": "…",
        "score": 0.0317,
        "semantic_score": 0.68,
        "filename": "handbook.txt",
        "page_number": 1,
        "char_start": 0,
        "char_end": 120,
        "metadata": {
          "retrieval_source": "hybrid",
          "rerank_status": "passthrough",
          "reranker_provider": "noop",
          "reranker_score_scale": "reciprocal_rank_fusion",
          "source_revision_id": "…",
          "source_group_id": "…",
          "source_title": "Refund policy",
          "source_lifecycle_status": "active",
          "source_role": "primary",
          "index_build_id": "…",
          "source_metadata_generation": 12,
          "configuration_hash": "…"
        }
      }
    ],
    "diagnostics": {
      "strategy": "hybrid",
      "duration_ms": 42,
      "rerank_requested": true,
      "rerank_status": "passthrough",
      "reranker_provider": "noop",
      "reranker_model": "noop",
      "reranker_version": "1",
      "reranker_score_scale": "reciprocal_rank_fusion",
      "best_semantic_score": 0.68,
      "query_language_profile": "latin_ambiguous",
      "translation_status": "applied",
      "translation_source_language": "en",
      "translation_target_language": "bn",
      "translation_provider": "openai",
      "translation_model": "gpt-5-nano",
      "translated_query": "উৎসে কর সংগ্রহের খাত",
      "executed_branches": [
        "original_dense",
        "original_lexical",
        "translated_dense:bn",
        "translated_lexical:bn"
      ],
      "selected_trace": [
        {
          "rank": 1,
          "chunk_id": "…",
          "rrf_score": 0.0475,
          "original_dense": { "rank": 8, "score": 0.18, "rrf": 0.0147 },
          "translated_dense": { "branch_id": "translated_dense:bn", "rank": 1, "score": 0.71, "rrf": 0.0164 },
          "translated_lexical": { "branch_id": "translated_lexical:bn", "rank": 1, "score": 12.4, "rrf": 0.0164 }
        }
      ],
      "index_build_id": "…",
      "source_metadata_generation": 12,
      "source_policy_configured_mode": "enforce",
      "source_policy_effective_mode": "observe",
      "source_policy_deployment_cap": "observe",
      "source_policy_status": "observed",
      "source_policy_exclusion_reasons": { "draft": 1 },
      "source_policy_consolidation_reasons": {},
      "configuration_hash": "…"
    }
  }
}
```

The production default keeps fused RRF order through the enabled rerank stage with a
`noop` occupant (`rerank_status=passthrough`, `reranker_score_scale=reciprocal_rank_fusion`).
Pass-through does not load chunk text or rewrite ranking scores. Hybrid search always runs
original dense and original lexical branches; one target-language translation pair is added only
when query translation is enabled and the active build has language inventory. Public search hits
keep translated variant identity without the translated query text unless persistence is enabled.
Internal chat and evaluation keep the runtime text for grounding. It is not copied into citations,
evidence excerpts, or application logs. On an enabled reranker failure, search still returns fused
RRF order and diagnostics report `rerank_status=unavailable`; quality runs count this path against
candidate promotion.

Semantic and keyword SQL join the same Knowledge-owned source scope captured at one Project source
generation. `off` preserves legacy results, `observe` reports decisions without filtering, and
`enforce` excludes inapplicable revisions before ranking and consolidates lower-ranked revisions
only within the same source group. The deployment cap
`APE_AI_POLICY__SOURCE_POLICY_MODE` is the deployment default (`off`) inherited when a Project
omits the leaf. `APE_AI_POLICY__SOURCE_POLICY_DEPLOYMENT_CAP` can lower an effective Project or
global mode without a schema or Project-policy rollback; the cap never raises `off` to
`observe`/`enforce`. Missing/unspecified legacy metadata stays neutral. In `enforce` mode,
retrieval over-fetches the bounded candidate window before same-group consolidation so distinct
sources can still fill the requested `top_k` where available.

`source_policy_exclusion_reasons` counts only excluded source rows. An applicable result never
carries `source_policy_exclusion_reason` in its metadata or citation provenance. For an explicit
historical `as_of`, a governed document with no revision effective on that date is counted as
`not_applicable` rather than being treated as neutral legacy metadata.

## Re-embed after an embedding dimension change

Migration `0026` changes the deployment-wide column to `vector(1024)`, clears
incompatible retrieval artifacts, invalidates builds/pointers, and returns affected
documents to `chunked`. Rebuild them through the unchanged lifecycle endpoints:

1. `POST /api/v1/projects/{project_id}/documents/{document_id}/embed`
2. Poll until `embedded`, then call `POST .../index`
3. Poll until `ready` and validate semantic/hybrid search

Bulk re-embedding remains an operator/admin-script concern.

Operational sequence and validation queries:
[pgvector operations runbook](../learning/pgvector-operations-runbook.md).
