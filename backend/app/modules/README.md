# Feature Modules

Vertical slices for business capabilities. Each module owns services,
repositories, and schemas. **ORM models** live in [`app/models/`](../models/).

## Internal structure

```text
app/models/                   # All SQLAlchemy entities (shared, registered for Alembic)
modules/<feature>/
    services/                 # Business orchestration + transaction control
    repositories/             # AsyncRepository or ProjectScopedRepository subclasses
    schemas/                  # Pydantic request/response models
    workflows/                # Optional — complex pipelines only
```

HTTP routers are wired in the **composition layer** (`api/v1/routes/<feature>.py`).
Module code must **not** import `app.dependencies`.

Register ORM models in `app/composition/orm_registry.py` for Alembic.

## Planned bounded contexts (Phase 1+)

| Module | Responsibility |
| ------ | -------------- |
| `projects` | Central aggregate — Project CRUD, membership, per-Project config |
| `knowledge` | Ingestion pipeline: connectors, documents, parsing, chunking, embeddings, storage orchestration |
| `retrieval` | Hybrid search, reranking, context building |
| `conversations` | Chat, prompts, conversation memory |
| `evaluation` | Datasets, evaluation runs, feedback |

Internal components (chunking, OCR, file upload) start inside `knowledge` until
a separate boundary is justified.

## Dependency rules

| From | May import |
| ---- | ---------- |
| `modules/<x>/` | `core`, `platform`, `app.models`, own module |
| `modules/<x>/` | **NOT** `dependencies`, `api`, `composition`, other modules |
| `api/v1/routes/` | `core`, `platform`, `modules`, `dependencies`, `app.models` |
| `dependencies/` | `core`, `platform`, `modules` (wiring only) |
| `platform/` | `core` only |
| `composition/` | anywhere (ORM registry, startup) |

Enforced by `tests/architecture/test_import_boundaries.py`.

## Adding a module

1. Add ORM models under `app/models/` and register in `app/composition/orm_registry.py`.
2. Create `modules/<name>/` following the structure above.
3. Add routes in `api/v1/routes/<name>.py` and register on `api/v1/router.py`.
4. Add Alembic migration.
5. Add `docs/features/<name>.md` and tests.

See `modules/_template/README.md`.
