# Evaluation API

Versioned Project-scoped quality datasets, durable runs, and the latest quality summary.

**Prefix:** `/api/v1/projects/{project_id}/evaluations`

## POST `/datasets`

Create an immutable dataset version. Returns **201**.

```json
{
  "name": "pilot-quality",
  "version": "1.0.0",
  "schema_version": 2,
  "cases": [
    {
      "key": "english-to-bangla",
      "kind": "cross_lingual",
      "query": "How long is the refund window?",
      "query_language": "en",
      "expected_evidence_language": "bn",
      "relevant_chunk_ids": ["<bangla-chunk-uuid>"]
    },
    {
      "key": "refund-citation",
      "kind": "citation",
      "query": "How long is the refund window?",
      "relevant_chunk_ids": ["<chunk-uuid>"],
      "expected_answer_tokens": ["thirty"]
    },
    {
      "key": "unsupported",
      "kind": "no_answer",
      "query": "What is the lunar payroll rule?",
      "expected_no_answer": true
    }
  ]
}
```

Duplicate `(project_id, name, version)` returns `evaluation_dataset_version_exists`.

## GET `/datasets`

List dataset versions newest first. Query: `limit`, `offset`.

## POST `/runs`

Queue a durable comparison run. Returns **202**.

```json
{ "dataset_id": "<dataset-uuid>", "top_k": 5 }
```

The response contains `job_id`, `job_state`, `configuration_hash`, and `versions`. Poll the run or
the [Jobs API](jobs_api.md).

## GET `/runs`

List runs newest first, including metrics, regressions, failed cases, and reranker comparison.
Metrics include false-refusal and false-accept rates, unverified-claim rate, per-language-pair
Recall@k/nDCG, and semantic-score positive/hard-negative calibration distributions.

## GET `/runs/{run_id}`

Get one run. Cross-Project IDs return `evaluation_run_not_found`.

## GET `/quality`

Return the latest run, its immutable dataset, and current acceptance thresholds. Before the first
run, `dataset` is the latest available dataset. This is the console quality read model.


Production Evidence Quality evaluation now invokes the shared message runner,
including normalized request scope, production recovery, draft generation,
semantic verification and terminal reduction. Case results add `execution` and
`complete_turn_latency_ms`. Existing `latency_ms` remains retrieval latency for
compatibility. Evaluation does not create conversation messages; its transactions
and result persistence remain owned by the evaluation service. Timeouts remain
failed complete-turn outcomes and must not be treated as correct abstention.


Production evaluation starts the complete-turn clock before scope normalization and the first
search. Each case/profile gets a fresh search service and a separate read session; evaluation
run/job persistence retains its own transaction. Retrieval metrics come from that first scoped
search, using the dataset filters and run top_k. Timeout and verification-failed cases are
execution failures, appear in failed_cases, and earn no no_result_behavior abstention credit.
The profile metrics include execution_failure_count.

### Production execution diagnostics

Evaluation public results carry sanitized execution, notices and lifecycle. Full
operator diagnostics are excluded from ordinary run/list/quality responses and
stored only for explicitly requested protected captures. The execution value is the same public
message.execution.v1 projection used by chat and persisted GET. Lifecycle records
request clocks, frozen estimates, stages, and persistence measurements; protected
operator diagnostics retain rejected assertions and their verifier failures.
Published claims contain supported facts only. An uncited generated draft cannot
become grounded by attaching every selected source during repair.


Dataset cases optionally accept `expected_outcome` (answered, partial,
insufficient_evidence, unresolved_authority); explicit outcomes determine the
expected-no-answer flag. Existing cases retain their legacy interpretation.
Results include explicit search and complete-turn latency, no-fact citation status
and attempted/rejected/published assertion counts. Profile metrics distinguish
eligible full, partial and abstention denominators and execution failures.
Legacy latency aliases still measure search only. Missing complete-turn latency
is null, not an inferred measurement.

### Protected evaluation capture

`POST /api/v1/projects/{project_id}/evaluations/runs/captured` accepts the normal
run-create body but requires operator authentication and browser CSRF. It records
an audited opt-in. Ordinary `POST .../runs` never captures detailed proof.
`GET .../runs/{run_id}/diagnostics` requires operator access and audits the read.
New payloads are allowlisted, capped at 262,144 bytes per captured case/profile,
and expire after seven days; reads and writes remove expired payloads.
The application retention sweep clears expired payloads every 60 seconds without
requiring a later evaluation read or capture; unexpired payloads remain available.

Ordinary run, list and quality projections recursively strip private diagnostic
keys, including legacy stored records. The additive migration preserves historical
records. It does not retroactively establish deletion or seven-day expiry for old
inline payloads; those retained private database records require a separately
approved retention operation. They are not exposed through ordinary APIs.

### Candidate evaluation request

`POST /api/v1/projects/{project_id}/evaluations/runs/captured` requires Super Admin
and browser CSRF, and returns **202** with the normal durable run envelope:

```json
{
  "dataset_id": "<dataset-uuid>",
  "top_k": 5,
  "preview_index_build_id": "<sealed-candidate-build-uuid>"
}
```

Omit `preview_index_build_id` to evaluate the active corpus. When supplied, the build
must be Project-scoped, `validated`, structurally intact and compatible with the
resolved embedding identity. `preview_build_unavailable` and
`preview_embedding_mismatch` are **400** errors. The ordinary `POST /runs` rejects
candidate pins with `preview_requires_admin`. Run `index_build_id`, `versions` and
`config_provenance` identify the exact candidate snapshot; no pointer activation occurs.


The shared execution result's completion now reflects verified published required
assertions and dependencies, with exact omitted-requirement notices. Source coverage
alone cannot turn an incomplete generated or repaired answer into a complete result.
