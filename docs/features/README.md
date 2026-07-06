# Feature Documentation

Concise, per-feature reference documentation for the AI Platform Engine.

## Shipped features

| Feature | Doc | Scope |
| ------- | --- | ----- |
| Project Management | [project_module.md](./project_module.md) | CRUD, `is_active` toggle, soft-delete |
| Knowledge | [knowledge_module.md](./knowledge_module.md) | Upload → parse → chunk (`status=chunked`) |
| Retrieval | [retrieval_module.md](./retrieval_module.md) | Embed → index → semantic search (`ready`, ADR-007 baseline) |
| Conversations | [conversation_module.md](./conversation_module.md) | RAG chat, stateful conversations, SSE streaming (ADR-008) |

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
