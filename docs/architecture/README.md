# Architecture Documentation

Authoritative technical reference for **how** the AI Platform Engine (APE) is built.

## Start here

| Document | Description |
| -------- | ----------- |
| **[Module architecture](./module-architecture.md)** | **Canonical** repo layout, dependency rules, composition root |
| [System architecture guide](./system-architecture.md) | Overview, request flow, doc index |

## By concern (one canonical doc each)

| Concern | Document |
| ------- | -------- |
| Module layout & imports | [module-architecture.md](./module-architecture.md) |
| Project ownership | [domain-ownership.md](./domain-ownership.md) |
| Provider interfaces | [provider-architecture.md](./provider-architecture.md) |
| Configuration hierarchy | [configuration-architecture.md](./configuration-architecture.md) |
| Background jobs | [background-processing.md](./background-processing.md) |
| Deployment topology | [deployment-architecture.md](./deployment-architecture.md) |
| Long-term decisions | [adr/](./adr/README.md) |

## Canonical sources (rules)

- `.cursor/rules/project-context.mdc` — vision, scope, status
- `.cursor/rules/architecture.mdc` — engineering standards

Narrative docs here elaborate on those rules and must not contradict them.

## Repository layout (summary)

```text
backend/app/
  composition/  ORM registry for Alembic
  api/          Composition root (health + /api/v1 + routes/)
  dependencies/ FastAPI DI (composition only)
  core/         Cross-cutting kernel
  platform/     Shared technical infrastructure
  modules/      Feature vertical slices (bounded contexts)
```

Layering inside each module: Router → Service → Repository / Provider.
