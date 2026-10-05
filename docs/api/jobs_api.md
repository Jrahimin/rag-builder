# Jobs API

Inspect and retry durable ingestion/indexing work. All routes require an
Organization API key and verify that `{project_id}` belongs to that Organization.

**Prefix:** `/api/v1/projects/{project_id}/jobs`

## GET ``

List newest jobs with `limit` (1–100), `offset`, and optional `state`,
`job_type`, or `document_id` filters. Job types are `document.process`,
`document.embed`, `document.index`, `corpus.reembed`, `corpus.reindex`,
`document.delete`, `document.purge`, and `storage.reconcile`; states are `queued`, `running`,
`retry_scheduled`, `succeeded`, and `failed`.

The paginated response exposes stage/progress, attempts, lease/heartbeat,
timestamps, document/configuration identities, and structured failure fields.

## GET `/{job_id}`

Return the Project-scoped job plus its payload and immutable configuration
provenance: `configuration_hash`, `configuration_schema_version`, and normalized
`configuration`. Configuration responses never contain provider credentials.

Wrong-Project and unknown identities both return `404 job_not_found`.

## POST `/{job_id}/retry`

Returns `202` and retries a terminal failed job. The response is a new queued JobRun with a new
identity, `retry_of_job_id` pointing to the failed run, and the same immutable
configuration snapshot. A non-failed job returns `400 job_not_retryable`.

The retry and its dispatch intent are committed before Redis dispatch. Redis
failure therefore does not lose the accepted retry.

## Async action compatibility

Upload returns `201`; reprocess, embed, and index return `202`. Their Document
response includes nullable `data.job_id`. Save this identity when
you need detailed operational progress or explicit failure retry; continue using
`Document.status` to decide when the corpus is product-ready.


### Waiting for build quality acceptance

Document delete/purge jobs can return `waiting_acceptance`, stage
`awaiting_quality_acceptance`, unfinished result `destructive_work=not_started`
and no `completed_at`. This is durable pending work. An accepted active candidate
makes the dispatcher publish one continuation through the normal outbox and worker
lease. Restarts/duplicate deliveries cannot publish success or remove data early.
Only completed resumed work transitions to `succeeded`.

Local journey tooling reports `waiting_acceptance` as an explicit job/build
quality obligation immediately. It does not count that state as completed work or
retry it as a provider failure. Approved activation plus the normal accepted-job
resumption/outbox path is still required before purge can complete.
