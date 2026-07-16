# Implementation Plans

Feature and sprint implementation plans for APE. Each plan is a self-contained
design document written before implementation begins.

| Plan | Status | Description |
| ---- | ------ | ----------- |
| [project_module_plan.md](./project_module_plan.md) | Complete | Phase 1 — Project Management (CRUD, `is_active` toggle, soft-delete via `deleted_at`) |
| [knowledge_module_plan.md](./knowledge_module_plan.md) | Complete | Knowledge — upload → parse → chunk (v1 at `chunked`) |
| [retrieval_module_plan.md](./retrieval_module_plan.md) | Complete | Retrieval — embed → index (Qdrant) → semantic search (v1 at `ready`) |
| [pgvector_migration_plan.md](./pgvector_migration_plan.md) | Proposed | Replace Qdrant with PostgreSQL-native pgvector retrieval across code, local Docker, and self-hosted deployments |
| [conversation_module_plan.md](./conversation_module_plan.md) | Complete | Chat — RAG generation, stateful conversations, SSE streaming |
| [organization_auth_module_plan.md](./organization_auth_module_plan.md) | Complete | Organization API key auth, multi-key rotation, verified-key cache, Redis rate limiting |

Plans are superseded by `docs/features/` once a capability ships.

**Canonical location:** keep this directory in sync with Cursor plan files when both exist.
