# Contextual Generation

## Purpose

External applications can keep retrieval, calculations, and business rules in
their own system while using RAG Builder for governed LLM generation. They send
trusted text or JSON context and receive a schema-validated, auditable result
without creating Documents, chunks, embeddings, or vector indexes.

## Architecture

```text
Organization auth + Project guard
        ↓
GenerationCreateRequest validation
        ↓
Use-case prompt + schema resolution
        ↓
Bounded grounded message construction
        ↓
BaseLLMProvider
        ↓
JSON Schema validation
        ↓
Project-scoped Generation trace
```

The vertical slice is `modules/generation/`. HTTP and provider construction stay
in `api/v1/routes/generations_router.py` and `dependencies/generation.py`.
`GenerationRepository` extends the same fail-closed
`ProjectScopedRepository` used by existing Project-owned resources.

Generation has no import or runtime dependency on knowledge, retrieval, or
conversations.

Project chat `response_mode` does not change this trust boundary. Caller context remains
authoritative, and contextual generation does not perform web enrichment. Its persisted trace
records `context_authority=caller_context`, `web_enrichment_allowed=false`,
`web_enrichment_used=false`, and `source_provenance=none` without exposing chat policy.

## Data flow and transactions

1. FastAPI authenticates the Organization and verifies Project ownership.
2. Pydantic validates the request. The service checks canonical byte size,
   context root type, depth, node count, and schema size.
3. The registry resolves a known use case, prompt version, and built-in or
   caller-provided schema identity.
4. The service commits a `processing` Generation row, reserving the hashed
   Project-scoped idempotency key.
5. No database transaction remains open during the LLM call.
6. Provider text is parsed when the schema requires JSON and validated with
   JSON Schema 2020-12.
7. A second transaction records success or a safe terminal failure, provider
   and model versions, token usage, timing, request ID, and trace ID.

Provider and schema failures therefore remain observable even though the create
request returns an error envelope.

## Prompt and schema policy

Callers send `use_case`, not a raw prompt. Registered templates define the
system behavior and explicitly delimit caller input and context as data.
Instructions embedded inside context do not become system instructions.
Free-text JSON Schema annotations such as `description`, `title`, and
`$comment` are stripped from the model-facing schema representation, while the
complete schema remains authoritative for validation.

The first registered use cases are `contextual_answer` and
`structured_summary`. New domain-agnostic use cases are added as versioned
registry entries with immutable default schemas. A caller schema receives a
content-derived `custom-...` version so traces remain reproducible.

The current `BaseLLMProvider` contract returns normalized text and has no shared
vendor-native structured-output option. Generation therefore uses the existing
provider call, prompts for the resolved schema, then performs authoritative
platform-side validation. Vendor-specific response types do not leak into the
module.

## Configuration

| Setting | Default |
| --- | --- |
| `APE_GENERATION__MAX_REQUEST_BYTES` | `262144` |
| `APE_GENERATION__MAX_CONTEXT_BYTES` | `204800` |
| `APE_GENERATION__MAX_SCHEMA_BYTES` | `32768` |
| `APE_GENERATION__MAX_CONTEXT_DEPTH` | `12` |
| `APE_GENERATION__MAX_CONTEXT_NODES` | `5000` |
| `APE_GENERATION__DEFAULT_RETENTION` | `none` |
| `APE_GENERATION__ALLOW_FULL_RETENTION` | `true` |

Provider defaults and credentials reuse `APE_LLM__*`. Caller `max_tokens` is
bounded by the deployment maximum.

## Retention and security

- `none` stores no raw caller input or context.
- `metadata_only` stores hashes, sizes, and top-level shape only.
- `full` stores the exact JSON payload when deployment policy allows it.
- The response schema, output, prompt/schema identities, and usage trace are
  retained for reproducibility.
- The GET API never returns retained input/context.
- Idempotency keys are SHA-256 hashed before persistence.
- External JSON Schema references are rejected to prevent network/schema
  resolution outside the request.

## Testing

Unit tests cover success, invalid context, use-case resolution, output mismatch,
provider failure, idempotency, and every retention mode. PostgreSQL integration
tests exercise both routes, persisted terminal failures, wrong-Project hiding,
idempotent replay/conflict, and raw-payload retention.

## Future improvements

- Provider-capability-aware native structured output behind the existing LLM
  contract, once multiple providers expose a portable intersection.
- DB-backed Project prompt/schema configuration when the planned typed Project
  configuration resolver ships.
- Optional list/filter API if operational demand requires more than ID lookup.
