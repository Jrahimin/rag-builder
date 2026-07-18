# Operator Console MVP

## Purpose

Phase 3 adds a responsive internal console for operating one trusted dedicated deployment without
routine terminal or database access. It is mounted at `/operator/` and intentionally does not add
authentication, customer identity, RBAC, billing, chat, connector management, or a control plane.

## Architecture

```text
React feature screens
        ↓ TanStack Query
one typed operatorApiClient
        ↓ relative /api requests
Vite proxy (development) / Nginx proxy (production)
        ↓
existing FastAPI operator + project-scoped APIs
```

`frontend/src/api/generated/openapi.ts` is generated from FastAPI's OpenAPI schema. Feature
components never call `fetch`; `operatorApiClient.ts` owns envelopes, query encoding, and actionable
backend-unavailable errors. TanStack Query owns caching, bounded retries, polling, and retry-action
invalidation. State local to filters, selections, and mobile navigation stays in React.

## Screens and feature boundaries

| Route | Feature component | Operational purpose |
| --- | --- | --- |
| `/operator/` | `SystemHealthOverview` | Health, active work, failures, queue, dependency summary |
| `/operator/jobs` | `JobRuns`, `JobRunDetails` | All-projects-by-default or project-scoped list/detail polling and failed-job retry |
| `/operator/projects` | `ProjectDocumentInspection`, `DocumentLifecycleDetails` | Project/corpus selection and document lifecycle inspection |
| `/operator/configuration` | `ActiveConfigurationDetails` | Sanitized providers, runtime choices, index version, snapshots |
| `/operator/metrics` | `OperationalMetrics` | Queue, latency, token, corpus, and storage metrics |
| `/operator/quality` | `EvidenceQuality` | Dataset/config versions, latest metrics, regressions, failed cases, and reranker comparison |
| `/operator/audit` | `AuditHistory` | Recent immutable operator and durable-job events |
| `/operator/health` | `DependencyWorkerHealth` | Readiness checks, degraded dependencies, worker heartbeats |

Reusable primitives live in `src/components`: query states, status badges, metric cards, and project
selection. Feature files use descriptive domain names and remain separate from app composition.

## Query and failure behavior

- Overview and operational aggregates refresh every 10–30 seconds.
- Job lists poll every 3 seconds while work is active and every 15 seconds otherwise.
- An open active job detail polls every 2 seconds.
- Safe retry is enabled only for terminal failed jobs. The backend performs the authoritative check.
- Empty projects, documents, jobs, audit, latency, and worker pools have explicit states.
- Quality runs poll while their durable job is active; the only quality mutation queues a run for an
  existing immutable dataset version.
- Degraded dependencies and an unavailable worker registry remain visible without hiding healthy data.
- Network failure renders a useful backend-unavailable state, including frontend-only Compose mode.

## Deployment

Vite serves `/operator/` with fast refresh and proxies `/api` to the backend during development.
The multi-stage production image builds static assets and serves them from Nginx with SPA fallback,
same-origin `/api` proxying, and a container health check. One root Compose file supports full-stack,
backend/infrastructure-only, and frontend-only service targeting.

## Testing

Vitest and Testing Library cover routing, loading/empty states, backend errors, degraded readiness,
active-job polling, job detail, safe retry, and quality metrics/version rendering. Static checks
include Prettier, ESLint, TypeScript, Vitest, and the Vite production build.

## Intentional limits

The console is read-only except for the existing failed-job retry action and queueing an evaluation
run against an existing immutable dataset. It does not create/edit datasets, add login, sessions,
cookies, users, provider credentials, configuration mutation, SSR, long-term time-series storage,
end-user chat, or customer administration.
