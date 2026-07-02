# Feature Documentation

Concise, per-feature reference documentation for the AI Platform Engine.

## Shipped features

| Feature | Doc | Scope |
| ------- | --- | ----- |
| Project Management | [projects.md](./projects.md) | CRUD, `is_active` toggle, soft-delete |
| Knowledge | [knowledge.md](./knowledge.md) | Upload → parse → chunk (`status=chunked`) |
| Retrieval | [retrieval.md](./retrieval.md) | Embed → index → semantic search (`ready`, ADR-007 baseline) |

## Template

Every feature document should cover:

- **Purpose** — what problem it solves and why it exists.
- **Architecture** — components involved and how they fit the layering.
- **Data flow** — the request/processing path (diagram preferred).
- **Configuration** — relevant settings and their defaults.
- **Dependencies** — providers, services, and infrastructure required.
- **Design decisions** — notable trade-offs.
- **Production considerations** — scaling, failure modes, observability.
- **Testing strategy** — how the feature is verified.
- **Future improvements** — known gaps and next steps.

Implementation plans live in [docs/plans/](../plans/) until superseded by feature docs here.
