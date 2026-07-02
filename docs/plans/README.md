# Implementation Plans

Feature and sprint implementation plans for APE. Each plan is a self-contained
design document written before implementation begins.

| Plan | Status | Description |
| ---- | ------ | ----------- |
| [projects-module.md](./projects-module.md) | Complete | Phase 1 — Project Management (CRUD, `is_active` toggle, soft-delete via `deleted_at`) |
| [knowledge-module.md](./knowledge-module.md) | Complete | Knowledge — upload → parse → chunk (v1 at `chunked`) |
| [retrieval-module.md](./retrieval-module.md) | Complete | Retrieval — embed → index (Qdrant) → semantic search (v1 at `ready`) |

Plans are superseded by `docs/features/` once a capability ships.

**Canonical location:** keep this directory in sync with Cursor plan files when both exist.
