# ADR-006: API and Worker Process Separation

**Status:** Accepted  
**Date:** 2026-06-28

## Context

Production deployments need independent scaling of HTTP serving vs background
processing.

## Decision

- **API process:** FastAPI + Uvicorn (development reload; two direct Uvicorn
  workers in the single-VPS production profile)
- **Worker process:** Taskiq worker consuming Redis queue (Phase 1)
- Both share `platform/` and `modules/` code; different entrypoints
- Root Compose uses production images for local full-stack and VPS operation;
  fast reload runs API/frontend processes on the host.

## Consequences

- Docker Compose runs API and worker as separate services
- Deployments scale API and worker replicas independently
- Shared Redis required for queue

## Alternatives considered

| Alternative | Rejected because |
| ----------- | ---------------- |
| API-only process running jobs inline | Blocks event loop; forbidden for AI tasks |
| One process per job type | Operational overhead without benefit at current scale |
