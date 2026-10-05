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


## Build acceptance

- `GET /api/v1/projects/{project_id}/index-builds/{build_id}/acceptance`: authenticated
  operator; lists immutable project/build quality receipts.
- `POST /api/v1/projects/{project_id}/index-builds/{build_id}/acceptance`: authenticated
  operator with browser CSRF; appends a validated `build.acceptance.v1` artifact.
  Identical artifact hashes are idempotent. Cross-project, failed or stale claims
  are rejected. OpenAPI lists every identity and report-case field.

Activation/rollback retain existing project authorization and now also require a
current operator-attested receipt. `index_acceptance_missing/stale/failed/incomplete`
are 400 responses; active pointer remains unchanged. Creating a receipt is not an
activation request. Offline hash/echo receipts are not live certification.

### Approved acceptance sets

`POST /api/v1/projects/{project_id}/index-builds/{build_id}/acceptance-set` requires
operator access and browser CSRF. The `acceptance.set.v1` definition binds project,
sealed build, revision, certification, case expectations, repetitions and comparison
obligations. Identical repeated submission is idempotent; replacement is rejected.
The definition hash must match `build.acceptance.v1.acceptance_set_hash`. A missing,
changed or substituted set cannot authorize activation. Production sets require
three repetitions and comparison evidence. The captured remediation corpus retains
its mandated question set; unrelated projects may define their own reviewed set.

### Build scope reviews and observed reports

These endpoints require Super Admin and Project authorization; browser writes also
require CSRF. Prefix: `/api/v1/projects/{project_id}/index-builds/{build_id}`.

| Method and suffix | Purpose |
| --- | --- |
| `GET /acceptance-identity` | Return secret-free runtime/configuration, corpus and semantic structure identity; audited read. |
| `POST /scope-reviews` | Publish an immutable exact-source `scope.v2` overlay on a sealed private candidate. |
| `POST /acceptance-reports` | Persist observed production Message comparison proof. |

Scope review request (hash placeholders represent lowercase SHA-256 values):

```json
{
  "chunk_id": "<chunk-uuid>",
  "source_revision_id": "<source-revision-uuid>",
  "source_generation": 7,
  "source_content_hash": "<sha256>",
  "chunk_hash": "<sha256>",
  "facts": [{
    "version": "scope.v2",
    "kind": "period",
    "value": "2025-2026",
    "legal_kind": "assessment",
    "start_year": 2025,
    "end_year": 2026,
    "scope": "governing",
    "locality": "provision",
    "locality_id": "<provision-id>",
    "effect": "operative",
    "exhaustive": false,
    "status": "reviewed",
    "source_span": {
      "text": "<exact source quote>",
      "char_start": 100,
      "char_end": 120,
      "provenance": "exact_source_span"
    },
    "review_provenance": {
      "reviewer": "<reviewer-id>",
      "evidence_hash": "<quote-sha256>",
      "reason": "<source-backed review reason>"
    }
  }]
}
```

Use actual quote lengths and document offsets. The service records the authenticated
reviewer. Response data includes review ID/hash, Project/build, chunk/revision/generation,
envelope, author and creation time. `scope_review_build_unavailable`, `scope_review_stale`,
`scope_review_source_mismatch`, `scope_review_invalid_span`, `scope_review_invalid` and
`scope_review_immutable` are **400** errors. Requests are bounded to 100 facts,
6,000 characters per quote and a 512 KB envelope. Changed publication requires a new
candidate; identical reviews are idempotent.

Observed report request structure:

```json
{
  "version": "acceptance.observed.v1",
  "compared_active_build_id": "<active-build-uuid>",
  "labels": {
    "<case-id>": {
      "question": "<exact approved question>",
      "expected": "answered",
      "reason": "<review reason>",
      "inventory_hash": "<candidate-semantic-structure-sha256>",
      "spans": [{
        "chunk_id": "<chunk-uuid>",
        "chunk_hash": "<sha256>",
        "quote": "<exact source quote>",
        "char_start": 100,
        "char_end": 120
      }],
      "missing_requirements": []
    }
  },
  "turns": [{
    "case_id": "<case-id>",
    "repetition": 1,
    "build_id": "<candidate-or-active-build-uuid>",
    "user_message_id": "<user-message-uuid>",
    "assistant_message_id": "<assistant-message-uuid>",
    "raw_message": { "id": "<assistant-message-uuid>", "content": "<captured content>", "claims": [], "citations": [], "metadata": {} }
  }],
  "parity": [{
    "assistant_message_id": "<assistant-message-uuid>",
    "transport": "get",
    "raw_message": { "id": "<assistant-message-uuid>" }
  }]
}
```

The example shows the shape; complete submissions supply actual public Message bodies,
three repetitions per approved definition case on both builds and both SSE/GET captures.
For remediation this is the immutable twelve-case, 72-Message matrix; the separate
sixteen-question historical baseline is optional. Additional reviewed labels/captures
do not expand required repetitions or release metrics. Parity retains all public
citation and assertion-evidence fields, normalizing optional schema defaults and
rejecting malformed or unknown nested proof fields. Answered/partial
labels require reviewed spans; every non-answered label requires named missing
requirements. The JSON-only API never opens caller-supplied server paths. Private
diagnostic/credential fields are rejected. Limits: 256 KB per Message, 8 MB per report,
100 labels, 600 turns, 20 parity captures.

Response data contains `id`, `project_id`, `build_id`, `report_hash` and normalized
`report`. Invalid/stale proof returns `acceptance_observed_invalid`; failed terminal
or assertion proof returns `acceptance_observed_failed` (**400**). Completed turns
must meet 45/120-second simple/complex ceilings and 30/90-second p95 targets.

Production `POST /acceptance` artifacts additionally supply `observed_report_id`,
`observed_report_hash`, `compared_active_build_id` and `comparison_report_hash` matching
the stored report. Cases and semantic structure hashes must agree. Receipt publication
and activation remain separate requests.


POST /acceptance-reports adds comparison_mode (active_candidate, default;
or first_build). First-build requests set compared_active_build_id=null and supply
three candidate repetitions only; the Project must have no active pointer or prior
activation history. Reports record baseline_status, release_build_id and
comparison_only_build_id. Subsequent requests retain both-build repetitions.
Failed active outcomes remain comparison evidence; candidate success and latency
release gates remain mandatory. Missing final persistence measurement rejects timing
certification. Rollback uses the exact retained target's original accepted report and
current runtime/configuration/source identity, without inventing a new comparison.

Search diagnostics add indexed_corpus_empty=true only for a sealed pinned build whose
manifest has no documents and whose keyword/vector/chunk counts are all zero. This
attested inventory gap completes an indexed-only limitation without further LLM
planning; an ordinary query miss does not establish corpus absence.
