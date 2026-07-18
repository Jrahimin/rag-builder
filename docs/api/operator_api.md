# Operator — deployment operations

All routes require the deployment admin key when authentication is enabled. Responses are
sanitized and never contain secret values.

The Phase 3 console consumes these endpoints with relative same-origin `/api` requests. In the
current trusted/internal deployment, leave `APE_AUTH__ENABLED=false`; the console adds no login,
session, cookie, user, or credential storage. If backend authentication is enabled, the existing
backend gates remain authoritative and unauthenticated console requests fail normally.

The console reuses project-scoped Projects, Documents, and Jobs APIs for inspection and safe retry.
In particular, `POST /api/v1/projects/{project_id}/jobs/{job_id}/retry` remains the only retry
action, preserving the Jobs service's eligibility check, immutable configuration snapshot,
transaction, audit event, idempotency key, and durable outbox dispatch.

## GET /api/v1/operator/overview

Combined dependency, worker, metric, and recent-failure status.

## GET /api/v1/operator/dependencies

Cheap live infrastructure checks plus cached startup provider capability results.

## GET /api/v1/operator/workers

Active Taskiq workers, heartbeat age, process identity, version, and queue.

## GET /api/v1/operator/metrics

Job/queue/retry/failure, latency, token usage, corpus/storage, and active index-version metrics.

## GET /api/v1/operator/configuration

Allowlisted active runtime and provider configuration plus the most recent secret-free
configuration snapshot for each project.

## GET /api/v1/operator/failures

Recent terminal job failures. Query: `limit` (1–100, default 20).

## GET /api/v1/operator/audit-events

Recent immutable job audit events. Query: `limit` (1–200, default 50), `offset`.

## GET /metrics

Lightweight Prometheus-compatible current gauges. This unversioned scraper endpoint uses the same
admin-key gate.
