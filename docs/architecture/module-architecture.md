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
├── models/                 # All SQLAlchemy ORM entities (shared across modules)
├── api/                    # HTTP composition (mount routers)
│   ├── health.py           # Deployment-level probes
│   └── v1/
│       ├── router.py       # Aggregates versioned routes
│       └── routes/         # Per-feature routers + Depends() wiring
├── dependencies/           # FastAPI DI factories (composition only)
├── cli/                    # Operational CLIs (e.g. reindex_cli)
├── core/                   # Config, logging, exceptions, middleware
├── platform/               # Shared kernel (no feature imports)
│   ├── db/                 # PostgreSQL engine + session management
│   ├── infra/connectivity/ # Redis/Qdrant adapters (health only)
│   ├── persistence/        # AsyncRepository, ProjectScopedRepository
│   ├── domain/             # ORM mixins, lifecycle helpers, text_normalizer, tokenization
│   ├── auth/               # Auth contracts, domain events (cache invalidation)
│   ├── http/               # ApiResponse / ErrorResponse, pagination, auth headers
│   ├── infra/auth/         # Verified-key cache, rate limiter, event handlers
│   ├── system/             # HealthService
│   ├── providers/          # Errors + capability reference
│   ├── jobs/               # JobQueue.enqueue contract
│   └── config/             # ConfigLayer precedence model
└── modules/                # Feature vertical slices (no HTTP wiring, no ORM)
    └── <feature>/
        services/ repositories/ schemas/ [workflows/]
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
| `modules/<x>/` | `core`, `platform`, `app.models`, own package | `dependencies`, `api`, `composition`, other modules |
| `api/`, `dependencies/` | `core`, `platform`, `modules`, `app.models` | — |
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

- ORM: `ProjectScopedMixin` on Project-owned entities (pick mixins per entity)
- Persistence: `ProjectScopedRepository` — requires `project_id`, scopes every query
- Aggregate roots (e.g. `Project`): `AsyncRepository` — unscoped by design

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
| `projects` | Central data aggregate (shipped) |
| `organizations` | Tenant CRUD, API key lifecycle (shipped; ADR-012) |
| `knowledge` | Ingestion — upload, parse, chunk; ends at `status=chunked` (shipped) |
| `retrieval` | Embed → index → search (`chunked` → `embedded` → `ready`); hybrid + rerank shipped (ADR-009) |
| `conversations` | Chat — retrieve → prompt → LLM → answer + citations; stateful conversations (shipped) |
| `evaluation` | Quality measurement + feedback |

### Organizations vs projects

```text
modules/organizations/   tenant + API keys; publishes auth invalidation events
dependencies/auth.py     credential verification, cache, rate limit (composition)
modules/projects/        project CRUD; organization_id scoping on list/create/access
```

Auth verification is **not** in `modules/organizations/` services — it lives in `dependencies/auth.py`. Key revoke / org deactivate publish domain events; `VerifiedKeyCacheEventHandler` invalidates the cache centrally.

### Knowledge vs retrieval vs conversations boundary

```text
modules/knowledge/       upload → parse → chunk       ends at status=chunked
modules/retrieval/       embed → index → search        chunked → ready → POST /search
modules/conversations/   chat (RAG generation)         uses RetrievalPort + BaseLLMProvider
```

- **Knowledge** does not import retrieval.
- **Retrieval** reads shared ORM (`Document`, `DocumentChunk`) via its own thin repos — never imports `modules/knowledge/`.
- **Conversations** does not import `modules/retrieval/` internals; composition layer wires `RetrievalPort` adapter.
- **Composition layer** (`dependencies/`) wires delete cascade, worker handoffs, and cross-module adapters.

---

## Related

- [Domain ownership](./domain-ownership.md)
- [Provider architecture](./provider-architecture.md)
- [Configuration](./configuration-architecture.md)
- [Background processing](./background-processing.md)
- [Deployment](./deployment-architecture.md)
- [ADRs](./adr/README.md)
