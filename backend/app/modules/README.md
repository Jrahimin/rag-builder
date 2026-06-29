# Feature Modules

Vertical slices for business capabilities. Each module owns services,
repositories, schemas, and ORM models.

## Internal structure

```text
modules/<feature>/
    services/         # Business orchestration + transaction control
    repositories/     # Extend ProjectScopedRepository
    schemas/          # Pydantic request/response models
    models/           # ORM entities (compose platform.domain mixins)
    workflows/        # Optional — complex pipelines only
```

HTTP routers are wired in the **composition layer** (`api/v1/routes/<feature>.py`)
or via a router factory registered from `api/v1/router.py`. Module code must
**not** import `app.dependencies`.

Register ORM models in `app.composition.orm_registry` for Alembic.

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
| `modules/<x>/` (services, repositories, models, schemas) | `core`, `platform`, own module |
| `modules/<x>/` | **NOT** `dependencies`, `api`, `composition`, other modules |
| `api/v1/routes/` | `core`, `platform`, `modules`, `dependencies` |
| `dependencies/` | `core`, `platform`, `modules` (wiring only) |
| `platform/` | `core` only |
| `composition/` | anywhere (ORM registry, startup) |

Enforced by `tests/architecture/test_import_boundaries.py`.

## Adding a module

1. Create `modules/<name>/` following the structure above.
2. Add routes in `api/v1/routes/<name>.py` and register on `api/v1/router.py`.
3. Register ORM models in `app/composition/orm_registry.py`.
4. Add Alembic migration.
5. Add `docs/features/<name>.md` and tests.

See `modules/_template/README.md`.
