# Domain Ownership

> See [module-architecture.md](./module-architecture.md) for layout and rules.

**Project** is the central aggregate. Business data is Project-owned by default.

## Scopes

| Scope | Examples |
| ----- | -------- |
| **Deployment** | DB/Redis/Qdrant connections, process config, health probes |
| **Platform** | Deployment-wide defaults, job queue, provider registry (future) |
| **Project** | Documents, connectors, prompts, chats, evaluations, API keys |

`OwnershipScope` enum: `platform/domain/ownership.py`.

## Enforcement (code, not catalogs)

- `ProjectScopedMixin` on ORM entities
- `ProjectScopedRepository` on every Project-owned repository
- `project_id` on every provider call and background job (when implemented)
- API routes under `/api/v1/projects/{project_id}/...` (future)

No resource-catalog enums in code — ownership is documented here and enforced
through mixins and repositories.
