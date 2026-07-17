# Domain Ownership

> See [module-architecture.md](./module-architecture.md) for layout and rules.

**Project** is the central aggregate for business data. **Organization** is the tenant / auth aggregate (ADR-012).

## Scopes

| Scope | Examples |
| ----- | -------- |
| **Deployment** | PostgreSQL/pgvector, Redis and storage connections, process config, health probes, auth pepper + admin key |
| **Platform** | Deployment-wide defaults, durable jobs/outbox, executor transport, provider registry (future) |
| **Organization** | Tenant identity, Organization API keys, org-scoped rate limits |
| **Project** | Documents, connectors, prompts, chats, evaluations, embeddings |

`OwnershipScope` enum: `platform/domain/ownership.py`.

## Two boundaries

```text
Organization  →  who is calling (M2M API key)
Project         →  which corpus (project_id on all business data)
```

- **Auth:** `require_organization_api_key` binds `AuthenticatedOrganization`; admin key for `/organizations/**` only.
- **Data:** `ProjectScopedMixin` + `ProjectScopedRepository`; nested routes use
  `ensure_project_accessible(project_id, org_id)`. Project aggregate mutations
  use `ensure_project_owned` so ownership remains enforced while deleted-state
  conflict/idempotency behavior stays in `ProjectService`.

## Enforcement (code, not catalogs)

- `ProjectScopedMixin` on ORM entities
- `ProjectScopedRepository` on every Project-owned repository
- `projects.organization_id` FK + `get_by_id_for_organization` on project access
- `project_id` on every repository query, provider call, durable job,
  configuration snapshot, and outbox operation
- API routes under `/api/v1/projects/{project_id}/...` (documents, jobs, search, conversations, embed, index)

No resource-catalog enums in code — ownership is documented here and enforced
through mixins, repositories, and auth dependencies.
