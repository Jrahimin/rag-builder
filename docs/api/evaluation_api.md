# Evaluation API

Versioned Project-scoped quality datasets, durable runs, and the latest quality summary.

**Prefix:** `/api/v1/projects/{project_id}/evaluations`

## POST `/datasets`

Create an immutable dataset version. Returns **201**.

```json
{
  "name": "pilot-quality",
  "version": "1.0.0",
  "schema_version": 1,
  "cases": [
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

## GET `/runs/{run_id}`

Get one run. Cross-Project IDs return `evaluation_run_not_found`.

## GET `/quality`

Return the latest run, its immutable dataset, and current acceptance thresholds. Before the first
run, `dataset` is the latest available dataset. This is the console quality read model.
