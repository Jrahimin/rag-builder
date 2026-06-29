# ADR-002: Project as Central Aggregate

**Status:** Accepted  
**Date:** 2026-06-28

## Context

APE serves multiple customers’ workloads within one deployment. A clear isolation
boundary is required for data, configuration, cost, and security.

## Decision

**Project** is the central domain aggregate. Almost all business resources are
Project-owned (`project_id` mandatory). Three ownership scopes apply:

- **Deployment** — infrastructure and process configuration
- **Platform** — shared deployment-wide defaults
- **Project** — business data and per-Project AI configuration

ORM entities use `ProjectScopedMixin`. Repositories filter every query by
`project_id`.

## Consequences

- Consistent isolation across SQL, vector store, and object storage
- API design uses `/api/v1/projects/{project_id}/...` for business resources
- Project deletion requires coordinated cascade (Phase 1)

## Alternatives considered

| Alternative | Rejected because |
| ----------- | ---------------- |
| Organization-only scoping | Too coarse; Projects are the customer-facing unit |
| Deployment-wide documents | Violates multi-tenant isolation within a deployment |
| Optional `project_id` | Enables unscoped data paths — forbidden by architecture |
