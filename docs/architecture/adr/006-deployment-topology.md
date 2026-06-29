# ADR-006: API and Worker Process Separation

**Status:** Accepted  
**Date:** 2026-06-28

## Context

Production deployments need independent scaling of HTTP serving vs background
processing.

## Decision

- **API process:** FastAPI + uvicorn (dev) / gunicorn + uvicorn workers (prod)
- **Worker process:** Arq worker consuming Redis queue (Phase 1)
- Both share `platform/` and `modules/` code; different entrypoints
- Local dev: API in Docker compose today; worker container added in Phase 1

## Consequences

- Docker compose will gain a `worker` service
- Deployments scale API and worker replicas independently
- Shared Redis required for queue

## Alternatives considered

| Alternative | Rejected because |
| ----------- | ---------------- |
| API-only process running jobs inline | Blocks event loop; forbidden for AI tasks |
| One process per job type | Operational overhead without benefit at current scale |
