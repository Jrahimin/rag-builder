# Module Architecture

> **Canonical source** for repository layout, dependency rules, and composition.
> Other documents link here; do not duplicate the full rule set elsewhere.

APE is a **modular monolith**: `core/` + `platform/` kernel + `modules/` vertical
slices + `api/` / `dependencies/` / `composition/` as the composition root.

---

## Repository layout

```text
backend/app/
├── main.py                 # ASGI entry + lifespan
├── composition/            # ORM registry + Alembic migrations
├── api/                    # HTTP composition (mount routers)
│   ├── health.py           # Deployment-level probes
│   └── v1/
│       ├── router.py       # Aggregates versioned routes
│       └── routes/         # Per-feature routers + Depends() wiring
├── dependencies/           # FastAPI DI factories (composition only)
├── core/                   # Config, logging, exceptions, middleware
├── platform/               # Shared kernel (no feature imports)
│   ├── db/                 # PostgreSQL engine + session management
│   ├── infra/connectivity/ # Redis/Qdrant adapters (health only)
│   ├── persistence/        # ProjectScopedRepository
│   ├── domain/             # ORM mixins, OwnershipScope
│   ├── http/               # ApiResponse / ErrorResponse
│   ├── system/             # HealthService
│   ├── providers/          # Errors + capability reference
│   ├── jobs/               # JobQueue.enqueue contract
│   └── config/             # ConfigLayer precedence model
└── modules/                # Feature vertical slices (no HTTP wiring)
    └── <feature>/
        services/ repositories/ schemas/ models/ [workflows/]
```

---

## Dependency direction

```text
modules/ ──► platform/ ──► core/
                ▲
api/ ──► dependencies/ ──► modules/ (wiring only)
composition/ ──► modules/ (ORM registry)
```

| Package | May import | Forbidden |
| ------- | ---------- | --------- |
| `core/` | stdlib, third-party | `platform`, `modules`, `api`, `dependencies`, `composition` |
| `platform/` | `core` | `modules`, `api`, `dependencies`, `composition` |
| `modules/<x>/` | `core`, `platform`, own package | `dependencies`, `api`, `composition`, other modules |
| `api/`, `dependencies/` | `core`, `platform`, `modules` | — |
| `composition/` | anywhere needed for registry | — |

**Modules must not import `dependencies/`.** HTTP routes and `Depends()` live in
`api/v1/routes/` or `api/health.py`.

Enforced by `tests/architecture/test_import_boundaries.py`.

---

## ORM discovery (Alembic)

Register models in `app/composition/orm_registry.py`. Alembic imports that file;
`platform/` never imports `modules/`.

---

## Project isolation (fail-closed)

- ORM: `ProjectScopedMixin` on every Project-owned entity
- Persistence: `ProjectScopedRepository` — requires `project_id`, scopes every query, deterministic `order_by`
- Unscoped `BaseRepository` is internal (`_base_repository.py`), not exported

---

## Provider SDK isolation

- Vendor SDKs: `platform/providers/implementations/` only
- Connectivity adapters: `platform/infra/connectivity/` (health/lifespan only)
- No `Redis` / `QdrantClient` in `dependencies/` public DI

Concrete provider interfaces are added with the first implementation.

---

## Planned modules (bounded contexts)

| Module | Scope |
| ------ | ----- |
| `projects` | Central aggregate |
| `knowledge` | Ingestion (connectors, documents, parsing, chunking, embeddings) |
| `retrieval` | Hybrid search + reranking |
| `conversations` | Chat + prompts |
| `evaluation` | Quality measurement + feedback |

---

## Related

- [Domain ownership](./domain-ownership.md)
- [Provider architecture](./provider-architecture.md)
- [Configuration](./configuration-architecture.md)
- [Background processing](./background-processing.md)
- [Deployment](./deployment-architecture.md)
- [ADRs](./adr/README.md)
