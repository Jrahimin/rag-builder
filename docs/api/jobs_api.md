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

Retry a terminal failed job. The response is a new queued JobRun with a new
identity, `retry_of_job_id` pointing to the failed run, and the same immutable
configuration snapshot. A non-failed job returns `400 job_not_retryable`.

The retry and its dispatch intent are committed before Redis dispatch. Redis
failure therefore does not lose the accepted retry.

## Async action compatibility

Upload, reprocess, embed, and index responses keep their existing Document
response and status code and add nullable `data.job_id`. Save this identity when
you need detailed operational progress or explicit failure retry; continue using
`Document.status` to decide when the corpus is product-ready.
