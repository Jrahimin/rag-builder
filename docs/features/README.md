# Feature Documentation

Concise, per-feature reference documentation for the AI Platform Engine.

> **Building an integration?** Start with the
> [Platform Integration Guide](../platform-integration-guide.md).

## Shipped features

- [Operator Console MVP](operator_console.md)

| Feature | Doc | Scope |
| ------- | --- | ----- |
| Organizations & auth | [organization_module.md](./organization_module.md) | Tenant CRUD, API keys, org-scoped rate limits (ADR-012) |
| Project Management | [project_module.md](./project_module.md) | CRUD, `is_active` toggle, soft-delete, org scoping |
| Knowledge | [knowledge_module.md](./knowledge_module.md) | Upload → parse → chunk (`status=chunked`) |
| Durable jobs | [jobs_module.md](./jobs_module.md) | Transactional dispatch, lease/retry recovery, inspection APIs |
| Production runtime + operator backend | [production_runtime_and_operator_backend.md](./production_runtime_and_operator_backend.md) | Certified profiles, preflight, readiness, metrics, workers, audit |
| Retrieval | [retrieval_module.md](./retrieval_module.md) | Embed → index → semantic + hybrid search (`ready`, ADR-007/009) |
| Conversations | [conversation_module.md](./conversation_module.md) | RAG chat, stateful conversations, SSE streaming (ADR-008) |
| Multilingual | [multilingual_support.md](./multilingual_support.md) | Unicode tokenization, FTS, OCR notes (ADR-010) |

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
