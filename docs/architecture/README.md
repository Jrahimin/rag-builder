# Architecture Documentation

Authoritative technical reference for **how** the AI Platform Engine (APE) is
built: layering, request flow, design decisions, and production concerns.

## Purpose

This section documents the engineering blueprint of the platform so any
contributor can understand the system and extend it consistently.

## Documents

| Document | Description |
| -------- | ----------- |
| **[System Architecture Guide](./system-architecture.md)** | Layering, request lifecycle, startup/shutdown, API surface, response contracts, where features plug in |

## What lives here (current and planned)

- **System architecture** — layers, boundaries, and request flow ✅
- **Cross-cutting concerns** — configuration, logging, error handling ✅ (in system guide)
- **Design decisions (ADRs)** — notable choices and trade-offs (planned)
- **Production topology** — scaling, security, deployment (planned)
- **AI data lifecycle** — ingestion → retrieval → generation (planned, Phase 1+)

## Canonical sources

The binding rules and principles are defined in the repository rules:

- `.cursor/rules/project-context.mdc` — product vision, scope, domain model, current status.
- `.cursor/rules/architecture.mdc` — engineering standards and layering.

Documents here elaborate on those rules; they must never contradict them.

## Layering at a glance

```text
Router (api/)  ->  Service (services/)  ->  Repository (repositories/)
                                        \-> Provider (providers/)  -> external infra
```

- Routers: validation, serialization, DI, Project scoping. No business logic.
- Services: business orchestration and transaction control.
- Repositories: relational persistence only (Project-scoped CRUD).
- Providers: vendor SDKs behind abstract interfaces (no SDK leakage).

## Related learning docs

Foundation mechanics are explained in depth under `docs/learning/`:

- `foundation-sprint-overview.md`
- `application-factory-and-fastapi.md`
- `configuration-system.md`
- `structured-logging.md`
- `database-and-migrations.md`
- `docker-local-development.md`
- `testing-strategy.md`
