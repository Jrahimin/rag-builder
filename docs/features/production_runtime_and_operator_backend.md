# Production Runtime and Operator Backend

## Purpose

Phase 2 makes an API and worker deployment fail fast when its configured production
capabilities are fake, incomplete, unreachable, or incompatible. It also gives the deployment
operator stable, admin-gated visibility without requiring direct database access. Phase 3 adds
the separate console UI described in [Operator Console MVP](operator_console.md).

## Certified production profiles

| Profile | LLM | Embeddings | Intended route |
| --- | --- | --- | --- |
| `hosted_openai` | `openai` | `openai` | Hosted OpenAI-compatible API and embedding endpoints |
| `private_ollama` | `ollama` | `ollama` | Private Ollama-compatible model endpoint |

Production also requires Taskiq, the durable dispatcher, hybrid retrieval, a non-noop
reranker, MinIO/S3-compatible storage, authentication, and non-default database, Redis, and
storage credentials. Enabled OCR must resolve to a real backend. Gemini and mixed production
provider combinations remain non-certified; development and tests retain hash/echo/local/noop
defaults.

## Startup and readiness flow

```text
Settings validation
      ↓
bounded startup preflight
      ├── PostgreSQL + pgvector + vector(n)
      ├── Redis
      ├── object-storage bucket access
      ├── one embedding probe + dimension validation
      ├── one LLM probe
      ├── reranker probe
      └── configured OCR initialization
      ↓
cache provider results for readiness/operator APIs
      ↓
start dispatcher or worker consumption
```

Production aborts before serving or consuming work if a required check fails. Development and
testing start in a degraded state so `/ready` can explain unavailable local dependencies.
`/ready` repeats only cheap infrastructure checks; provider calls are startup-only and returned
as cached results.

## Operator read model

The `modules/operations` slice is read-only and deployment-wide. It is the deliberate exception
to ordinary project-scoped reads and is reachable only through the deployment admin-key gate.
It aggregates:

- durable job states, queue/outbox age, attempts, failures, and job-type latency;
- worker availability from expiring Redis heartbeats;
- retrieval and generation timings already recorded on assistant messages;
- provider generation latency and token usage;
- project, document, chunk, and stored-byte counts;
- active deployment/index configuration and latest secret-free job snapshots;
- recent job failures and immutable project-scoped audit events.

The configuration response is allowlisted. It returns provider/model/version identifiers and
credential-presence booleans, never credential values or endpoint URLs.

## Audit behavior

Job submission, dispatch deferral, start, retry, recovery, success, and terminal failure stage an
`AuditEvent` in the same database transaction as the job transition. Audit detail is allowlisted
to identifiers, attempt counts, failure codes, and exception types. Raw provider or secret values
are not stored.

## Testing and failure behavior

Unit coverage exercises valid production startup, fake/noop providers, missing secrets,
unsupported profile combinations, embedding dimension mismatch, degraded Redis worker state,
operator database failure sanitization, and admin authorization. Integration coverage exercises
the operator metrics/config/failure/audit surfaces when the disposable PostgreSQL service is
available.

## Intentional limits

This backend slice does not add a provider marketplace, billing/cost calculation, Kubernetes,
public SaaS tenancy, customer authorization, or long-term time-series storage. The Phase 3
frontend consumes it without changing these limits. The metrics
endpoint exposes current database-derived aggregates; a later deployment may scrape them into an
external monitoring system.
