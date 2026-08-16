# Operator — deployment operations

All routes require an authenticated Super Admin browser session when authentication is enabled.
Responses are sanitized and never contain secret values. The session uses HttpOnly cookies; unsafe
requests also require the CSRF header described in [Authentication API](./authentication_api.md).

The operator console consumes these endpoints with relative same-origin `/api` requests. It
bootstraps the current Super Admin through `/api/v1/auth/me`, refreshes an expired access session
when possible, and stores no authentication token in browser storage.

The `/operator/lab` Test Lab also reuses the existing project-scoped Projects, Documents, Jobs,
Index Builds, Search, and Conversations APIs for browser-based end-to-end verification. It does
not introduce operator-only upload/search/chat contracts or a persisted test-run resource. Every
displayed result is an ordinary backend response, durable job state, immutable build state, or
persisted conversation message.

The console reuses project-scoped Projects, Documents, and Jobs APIs for inspection and safe retry.
In particular, `POST /api/v1/projects/{project_id}/jobs/{job_id}/retry` remains the only retry
action, preserving the Jobs service's eligibility check, immutable configuration snapshot,
transaction, audit event, idempotency key, and durable outbox dispatch.

## Canonical Project administration

The Operator console creates and administers Projects through `/api/v1/operator/projects`. Project
creation requires an explicit `organization_id`; the Test Lab only selects existing Projects.

- `GET|POST /api/v1/operator/projects`
- `GET|PATCH /api/v1/operator/projects/{project_id}`
- `PUT /api/v1/operator/projects/{project_id}/status`
- `DELETE /api/v1/operator/projects/{project_id}`
- `POST /api/v1/operator/projects/{project_id}/restore`
- `GET /api/v1/operator/projects/{project_id}/history`

Restore is deliberately conservative: restored Projects remain disabled until explicitly enabled.
The detail response includes ownership state and active AI/source configuration generations.

## GET /api/v1/operator/overview

Combined dependency, worker, metric, and recent-failure status.

## GET /api/v1/operator/dependencies

Cheap live infrastructure checks plus cached startup provider capability results.

## GET /api/v1/operator/workers

Active Taskiq workers, heartbeat age, process identity, version, and queue.

## GET /api/v1/operator/metrics

Job/queue/retry/failure, chat and contextual-generation latency, combined LLM
token usage, corpus/storage, and active index-version metrics.

## GET /api/v1/operator/usage

Persisted execution usage grouped by time bucket, Organization, Project, provider, model, and
workload (`chat`, `contextual_generation`, or `evaluation`). Optional filters are `start_at`,
`end_at`, `bucket=hour|day|month`, `organization_id`, `project_id`, `provider`, `model`, and
`workload`. Token totals remain null unless every selected execution has provider-reported input
and output usage; latency objects include their independent sample counts.

## GET /api/v1/operator/configuration

Allowlisted active runtime and provider configuration plus the most recent secret-free
configuration snapshot for each project.

## GET /api/v1/operator/failures

Recent terminal job failures. Query: `limit` (1–100, default 20).

## GET /api/v1/operator/audit-events

Recent immutable administrative and execution audit events. Query: `limit` (1–200, default 50),
`offset`, optional `organization_id`, and optional `project_id`. Organization and Project scopes
are independently nullable; resource type/id remains present for every event.

## Project AI policy

- `GET /api/v1/operator/provider-capabilities`
- `GET /api/v1/operator/projects/{project_id}/ai-config`
- `GET /api/v1/operator/projects/{project_id}/ai-config/revisions`
- `POST /api/v1/operator/projects/{project_id}/ai-config/revisions`
- `POST /api/v1/operator/projects/{project_id}/ai-config/revisions/{revision_id}/restore`

Revision creation requires `expected_active_revision_id` (null for the first revision) and an
audit reason. Restore creates a new revision by copying the selected historical document; it never
mutates or reactivates an old row in place. A stale expected pointer returns
`project_config_revision_conflict`.

The console bases an edit on the stored active sparse revision and merges the rendered changes into
that payload, preserving stored fields that are not shown in the current form. If the active
revision is not present in the fetched history, the console refuses to save rather than posting a
sparse replacement. Provider-capability responses describe a real configured model; the endpoint
does not invent a placeholder model for inactive providers.

## Legacy Project ownership

- `GET /api/v1/operator/projects/{project_id}/ownership/preflight?target_organization_id=...`
- `POST /api/v1/operator/projects/{project_id}/ownership/reassign`
- `POST /api/v1/operator/projects/{project_id}/ownership/confirm`

Only Projects migrated under the default Organization are unlocked. Reassignment requires the
expected current Organization, a valid target, and an audit reason, then locks ownership. Confirm
locks the current default Organization without changing IDs. Locked Projects reject all later
moves with `project_ownership_locked`.

## GET /metrics

Lightweight Prometheus-compatible current gauges. This unversioned scraper endpoint uses the same
Super Admin session gate.
