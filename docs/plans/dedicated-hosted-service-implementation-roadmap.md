# RAG Builder Implementation Roadmap

> **Canonical roadmap:** This is the repository’s canonical implementation roadmap. Read it together with [`.cursor/rules/architecture.mdc`](../../.cursor/rules/architecture.mdc) and [`.cursor/rules/project-context.mdc`](../../.cursor/rules/project-context.mdc). It defines implementation sequence; it does not authorize work outside the phase being undertaken.

RAG Builder is being prepared as a dedicated hosted, API-first RAG service with a private operator console. Supported self-hosting and customer-specific authorization are later, demand-led products.

## Roadmap at a glance

| Phase | Operational outcome | Readiness tag |
|---|---|---|
| 0 | Architecture alignment and foundation refinement | Alignment ready |
| 1 | Durable jobs and product-level job APIs | Personal API ready |
| 2 | Production runtime profile and operator backend | Hosted backend ready |
| 3 | Operator Console MVP | Personal / operator ready |
| 4 | Evidence quality and grounded answers | Pilot quality ready |
| 5 | Safe corpus and index lifecycle | Production ready |
| 6 | Hosted integration and commercial delivery | Sale ready |
| F1 | Supported self-hosted edition | Future |
| F2 | Customer-specific authorization | Future / demand |

## How to use this roadmap

- A phase is one complete Sol High implementation task. It is not complete when only models, interfaces, schemas, or screens exist. It must leave the runtime path operational, safe to operate, and supported by evidence.
- Preserve the documented modular monolith, provider abstraction, configuration-driven behavior, infrastructure isolation, test seams, versioning, and project scoping.
- Do not split a phase into smaller roadmap tasks. Sol may work incrementally in dependency order, but completes the full vertical slice in one task before the next phase begins.
- Every implementation prompt requires repository inspection, a short internal execution plan before editing, meaningful success and failure-path validation, exact checks run, and concrete completion-gate evidence in the final report.

## Personal-use cut line

| Stop point | Meaning |
|---|---|
| Minimum: Phase 1 | Controlled API integration into one personal project, operated directly. |
| Recommended: Phase 3 | Comfortable personal hosting: private console visibility for jobs, documents, health, configuration, metrics, and safe retries. |
| Trust-sensitive: Phase 4 | Required when answers influence decisions and need measurable retrieval, claim-linked evidence, and explicit insufficient-evidence behavior. |

## Simplification principle

> Simplify implementation where scope and code demonstrate that it should be simplified, without collapsing architectural boundaries. Keep essential abstraction; remove speculative abstraction.

- Do not replace interfaces with direct SDK or database usage, hardcode providers, merge modules for convenience, broadly restructure folders, replace frameworks, or redesign working architecture without a demonstrated benefit.
- Remove only dead paths, duplicated behavior, and unnecessary indirection after tracing callers and adding characterization coverage where behavior could change.

---

## Phase 0 — Architecture Alignment and Foundation Refinement

**Readiness:** Alignment ready — required before new product work.

### Outcome

Make the repository’s real flows, ownership, and extension seams understandable and clean before adding new production capabilities.

### Complete scope

- Trace the current upload, parsing, OCR fallback, chunking, embedding, indexing, retrieval, and chat flows from API entry points through services, workers, providers, repositories, and storage.
- Compare observed code behavior with the architecture and project-context rules and relevant architecture documents. Record discrepancies and update architecture documentation to describe the real implementation.
- Add or repair characterization tests before materially changing existing behavior, especially around lifecycle transitions, provider selection, project scoping, error responses, and worker/API boundaries.
- Remove verified dead paths and duplicated logic. Centralize duplicated configuration resolution, provider resolution, lifecycle handling, dependency wiring, and error mapping at their existing architectural ownership points.
- Improve names, module ownership, test seams, local setup, and developer navigation only where inspection shows a concrete maintainability or correctness gain.

### Completion gate

The end-to-end flows and their owners are documented from code; behavior-affecting refactors are characterized; removed paths have no live callers; duplicated cross-cutting paths have one clear owner; architecture docs match the running code; all existing supported flows remain operational.

### One-shot Sol High implementation prompt

```text
Implement Phase 0 as one complete architecture-alignment task in this repository. Before editing, read .cursor/rules/architecture.mdc, .cursor/rules/project-context.mdc, the relevant architecture docs, and trace the upload, parsing/OCR, chunking, embedding/indexing, retrieval, and chat paths in code. Produce and follow a short internal execution plan. Add or repair characterization tests before changing behavior. Simplify only demonstrated duplication, dead paths, unnecessary indirection, configuration/provider resolution, lifecycle handling, dependency wiring, and error mapping; keep each concern at its existing correct architectural owner. Preserve modular boundaries, provider contracts, configuration-driven behavior, infrastructure isolation, test seams, versioning, project scoping, and documented extension points. Do not replace interfaces with direct SDK/database use, hardcode providers, merge modules for convenience, broadly reorganize folders, replace frameworks, or redesign working architecture without demonstrated benefit. Update architecture documentation to reflect verified reality. Validate meaningful success and failure paths, run exact relevant checks, and finish with concrete gate evidence, changed-flow summary, removed-path evidence, and intentional non-goals. Do not implement Phase 1 capabilities in this task.
```

---

## Phase 1 — Durable Jobs and Product-Level Job APIs

**Readiness:** Personal API ready.

### Outcome

Replace document-status-driven worker coordination with persisted, recoverable job execution while retaining the Taskiq/Redis executor and existing modular-monolith boundaries.

### Complete scope

- Add a persisted, project-scoped `JobRun` model/repository/service with job type, state, stage, progress, attempt data, idempotency key, lease, heartbeat, timestamps, structured failure code, document reference, and immutable configuration-version reference.
- Add a minimal transactional outbox/dispatcher so committed work is not lost between PostgreSQL and Redis; Taskiq remains the executor.
- Make document process, embed, and index execution acquire leases, heartbeat, distinguish transient/permanent failures, retry safely, and recover expired runs.
- Keep `Document.status` as business lifecycle only. Make stage writes replay-safe so chunks, embeddings, keyword rows, and derived artifacts are not duplicated.
- Expose additive project-scoped job list, detail, and retry APIs, and return job identity from existing asynchronous actions without breaking existing clients.
- Store immutable normalized processing/index configuration snapshots with a hash; every job records the snapshot used.

### Correctness rules

- The database commit and dispatch intent must be durable; Redis availability must not create an unrecoverable committed-but-never-executed job.
- Each handler must be safe under retries, duplicate delivery, crash after partial progress, and stale-worker recovery.

### Completion gate

A worker or Redis interruption is recovered without manual database edits; committed work is durably dispatched; retry/replay cannot duplicate outputs; callers can inspect and retry a project-scoped job; existing upload-to-ready behavior remains operational.

### One-shot Sol High implementation prompt

```text
Implement Phase 1 end to end in this repository. First inspect the current background-processing, document lifecycle, configuration, repository, Taskiq/Redis, API-envelope, project-scoping, and Alembic paths; create a short internal execution plan before editing. Reuse those boundaries and add the persisted JobRun, immutable configuration snapshots, minimal transactional outbox, lease/heartbeat ownership, transient-versus-permanent retry handling, stale-run recovery, and idempotent document process/embed/index execution. Separate worker execution state from Document.status. Add additive project-scoped job list/detail/retry APIs and compatible job identifiers on async actions. Treat durable dispatch, recovery, replay safety, and idempotency as non-negotiable correctness requirements; validate both normal completion and interruption/retry/duplicate-delivery/failure paths. Complete schema, migration/backfill, service/repository, dispatcher/worker, dependency wiring, APIs, tests, and relevant docs as one vertical slice. Run exact relevant checks and report gate evidence, including the recovery and replay tests run. Do not add UI, webhooks, billing, new queues, or customer authorization.
```

---

## Phase 2 — Production Runtime Profile and Operator Backend

**Readiness:** Hosted backend ready.

### Outcome

Make a dedicated deployment deterministic, observable, and safe to operate with real providers before adding the console.

### Complete scope

- In production, reject hash embeddings, echo chat, noop dependencies, missing secrets, incompatible embedding dimensions, and unsupported provider capability combinations while preserving development/test behavior.
- Add bounded startup capability preflight for PostgreSQL/pgvector, Redis, object storage, configured LLM/embedding/reranker/OCR paths, and vector-dimension compatibility. Health probes must not repeatedly run expensive provider calls.
- Add worker availability and operational metrics for jobs, queue age, retries/failures, stage/provider/retrieval/generation latency, token usage, corpus/storage counts, and active configuration/index version.
- Extend health/readiness with actionable dependency states and expose a lightweight metrics endpoint using existing logging/health composition where possible.
- Add sanitized operator APIs for overview, dependencies, workers, metrics, active configuration, recent failures, and audit events; never return secret values.
- Certify one hosted OpenAI-compatible route and one private OpenAI-compatible/Ollama route. Keep other adapters non-certified rather than expanding the support matrix.

### Completion gate

A production process cannot start with fake or incompatible providers; an operator can determine dependency, worker, queue, job, configuration, and provider health through stable APIs and metrics without reading the database directly.

### One-shot Sol High implementation prompt

```text
Implement Phase 2 as one complete production-runtime slice. Inspect Settings, provider factories/contracts, configuration validation, health/readiness, logging, worker broker, audit, and deployment-admin dependencies; write a short internal execution plan before editing. Reuse and centralize the existing paths. In production, reject fake/noop providers, missing required secrets, dimension/schema mismatches, and unsupported provider combinations while preserving dev/test defaults. Add bounded preflight, worker availability, actionable readiness, and sanitized operator APIs/metrics for the specified runtime signals; never expose secret values or add a provider marketplace. Support only one certified hosted OpenAI-compatible profile and one certified private OpenAI-compatible/Ollama profile. Validate successful startup plus invalid provider, missing secret, dimension mismatch, degraded dependency, and metrics/API failure paths. Complete configuration, wiring, tests, docs, and deployment composition needed for an operational backend, run exact relevant checks, and report concrete gate evidence. Do not build the frontend, billing, Kubernetes, public SaaS tenancy, or customer authorization.
```

---

## Phase 3 — Operator Console MVP

**Readiness:** Recommended personal / operator ready.

### Outcome

Add the high-priority private console for operating one dedicated deployment; it is not an end-user RAG application or a customer control plane.

### Complete scope

- Inspect for an existing frontend. If none exists, add a small React + TypeScript + Vite application in a clear frontend directory and integrate it into local Docker without SSR, a large UI framework, or a separate control-plane backend.
- Use the Phase 1/2 operator APIs through one typed client. Use only a minimal deployment-admin gate derived from the existing admin secret and secure HttpOnly/Secure cookies; this is not customer identity, RBAC, SSO, or product authentication.
- Build Overview, Jobs, Projects/Documents, Configuration, Metrics, Audit, and System Health views with useful loading, empty, degraded, and failed states.
- Support safe job inspection/retry and show document lifecycle, parse/index status, active configuration/index version, worker heartbeat, failures, latency, usage, and dependency health.
- Keep configuration read-only except for safe backend actions already exposed. Never place provider secrets in browser storage or APIs.
- Integrate frontend build, type/lint/test commands, and production/local container serving; keep API access centralized.

### Completion gate

An operator can deploy the stack, enter the private console, understand health, inspect jobs/documents/configuration/metrics/audit history, and retry safe failures without routine terminal commands or direct database access.

### One-shot Sol High implementation prompt

```text
Implement Phase 3 as one complete operator-console vertical slice. Inspect the repository and Phase 1/2 APIs first, then create a short internal execution plan. Reuse any existing frontend; if none exists, add only a minimal React + TypeScript + Vite app. Use one typed API client and the existing deployment-admin boundary for a secure same-origin HttpOnly/Secure-cookie gate; do not create users, customer authentication, RBAC, SSO, or a general control plane. Build the specified operational screens and states, including job detail/retry and safe visibility of document, worker, dependency, configuration, index, latency, usage, failures, and audit data. Keep configuration read-only unless a backend action already exists and keep all secrets server-side. Complete the full UI, backend touchpoints, Docker/local serving, focused frontend checks, and docs. Validate loading, empty, degraded, failed, unauthorized-admin, safe-retry, and successful-operation paths; run exact relevant checks and report concrete gate evidence. Do not build end-user chat, billing UI, workflow builders, connector UI, or customer auth.
```

---

## Phase 4 — Evidence Quality and Grounded Answers

**Readiness:** Pilot quality ready.

### Outcome

Turn retrieval and generation quality into measured product behavior, then expose evidence-aware answers through the existing API and console.

### Complete scope

- Create a versioned evaluation dataset and runner using representative fixtures plus exact-token, paraphrase, metadata-filter, multilingual, no-answer, and citation cases.
- Record retrieval/configuration/model/prompt versions and establish baselines for Recall@k, MRR/nDCG, filtered correctness, latency, no-result behavior, groundedness, refusal, and citation coverage.
- Tune existing semantic/keyword candidate pools, RRF, metadata filtering, filtered pgvector behavior, context selection, and thresholds from measured results rather than new abstractions.
- Evaluate a small set of learned rerankers through the existing reranker contract and integrate one supported winner only when the evidence justifies it.
- Add structured claim-to-evidence output compatibly with existing citations: answer, claims/segments, source chunk/document/page or offset, grounded flag, and explicit insufficient-evidence reason.
- Add a console quality view for dataset/config versions, last run, metrics, regressions, failed cases, and reranker comparison.

### Correctness rules

- Reranker selection is a measured product decision, not a provider feature toggle. Compare candidates under the same corpus, queries, filters, and baseline configuration.
- Insufficient evidence is a first-class correct outcome; do not fabricate a grounded claim merely because generation succeeded.

### Completion gate

The team can reproduce a quality run, identify the exact configuration that produced it, demonstrate measured improvement from hybrid/reranked retrieval on target cases, return claim-linked evidence, and explicitly decline unsupported questions.

### One-shot Sol High implementation prompt

```text
Implement Phase 4 end to end without adding agents, GraphRAG, fine-tuning, or a separate evaluation platform. Inspect the current benchmark fixtures, retrieval ADR/features, semantic/keyword/RRF/reranker paths, ContextBuilder, PromptBuilder, citation snapshots, conversation APIs, and console; create a short internal execution plan before editing. Reuse those boundaries. Add a versioned evaluation dataset/runner, reproducible version capture, practical retrieval/latency/refusal/groundedness/citation metrics, measured hybrid tuning, compatible claim-to-evidence output, insufficient-evidence behavior, and console quality view. Select a learned reranker only by a documented comparison on the same representative dataset against the baseline, including retrieval gain, grounded-answer effect, latency, operational fit, and failure behavior; retain the comparison evidence and do not activate a candidate that does not meet the agreed acceptance threshold. Validate success, no-answer, filter, citation, streaming-compatibility, evaluation-regression, and reranker-unavailable paths. Complete tests, configuration, docs, and exact checks; report the selected reranker or the reason no candidate was promoted, plus concrete gate evidence. Keep the full ingest-search-chat flow operational.
```

---

## Phase 5 — Safe Corpus and Index Lifecycle

**Readiness:** Production ready.

### Outcome

Make every corpus-changing operation job-backed, isolated from the active corpus, and reversible where possible for a dedicated production instance.

### Complete scope

- Convert reprocess, re-embed, reindex, delete, and purge into durable idempotent jobs using Phase 1 rather than adding a second workflow system.
- Build on `document.version` and `embedding_set_version`. Add only the minimum immutable index-build and active-pointer state needed to build a new vector and keyword snapshot beside the active version, validate it, atomically activate it, retain the previous version, and roll back.
- Ensure retrieval reads only the active, complete version; partial, failed, cancelled, or superseded builds never replace a working corpus.
- Add MIME/signature validation, supported-type enforcement, corrupt/password-protected handling, stable failure codes, storage reconciliation, and a small malware-scanning boundary with a real production implementation and explicit development behavior.
- Define delete versus purge precisely and remove relational, vector, keyword, raw-file, parsed-file, and derived artifacts consistently.
- Extend the console with guarded lifecycle actions, progress, activation/rollback, confirmations, and audit records.

### Correctness rules

- A build is immutable after creation. Validation completes before it can become active; activation changes one authoritative pointer atomically.
- Rollback must be an explicit pointer change to a verified retained version, never a best-effort reconstruction of a failed build.

### Completion gate

A bad build cannot take down the active corpus; index activation and rollback are atomic; destructive operations are auditable and idempotent; unsupported or malicious files fail before expensive processing; storage and database state can be reconciled.

### One-shot Sol High implementation prompt

```text
Implement Phase 5 as one safe corpus-lifecycle slice. Inspect and reuse the Phase 1 job/outbox/retry framework, document.version, embedding_set_version, retrieval cleanup, storage providers, repositories, lifecycle helpers, and console; create a short internal execution plan before editing. Make reprocess, re-embed, reindex, delete, and purge durable idempotent jobs. Add only the minimal immutable index-build and active-pointer state required to build a full vector+keyword snapshot alongside the active snapshot, validate it, atomically activate it, retain the prior snapshot, and roll back. Treat immutable build isolation, atomic activation, and rollback as non-negotiable: search must never read a partial or failed build. Add file validation, stable failures, reconciliation, and the narrow malware-scanning boundary described above. Complete guarded console actions, migrations/backfill, compatibility, tests, docs, and exact checks. Validate success plus partial build, crash/retry, invalid file, scan failure, delete/purge, activation failure, rollback, and active-search isolation paths. Report concrete gate evidence; do not add connectors, legal hold, multi-region behavior, or a generic index platform.
```

---

## Phase 6 — Hosted Integration and Commercial Delivery

**Readiness:** Sale ready — dedicated hosted pilot.

### Outcome

Finish the integration and operating contract so a dedicated customer instance can be onboarded, supported, upgraded, backed up, and recovered repeatedly.

### Complete scope

- Add a small project-scoped webhook system with HMAC signatures, versioned events, event IDs, retry/backoff, delivery history, replay, endpoint disablement, and console inspection for document processing/index outcomes.
- Stabilize OpenAPI schemas, idempotency/error contracts, pagination, asynchronous job responses, claim/evidence responses, integration examples, and versioning policy. Do not add SDKs until the API settles.
- Add a dedicated-deployment profile with immutable images, TLS/reverse-proxy boundary, secret injection, resource limits, health probes, migrations, PostgreSQL/pgvector, Redis, object storage, workers, and console. Do not introduce Kubernetes initially.
- Implement and exercise backup, restore, upgrade, rollback, reindex, key-rotation, incident, and support-diagnostic procedures. Expose only safe operational status in the console; keep dangerous execution operator-controlled.
- Document approved onboarding configuration, supported provider/file matrix, responsibility boundaries, and approved usage-cost visibility. Surface unresolved commercial decisions instead of inventing them.

### Correctness rules

- Webhooks are a bounded integration feature, not a general event platform. Delivery attempts must be auditable and replay-safe.
- Commercial policy is an input, not an implementation assumption. Record open decisions without creating unsupported commitments in code or documentation.

### Completion gate

A new dedicated instance can be created from the supported profile; a customer application can integrate through stable APIs/webhooks; operators can diagnose delivery failures; backup restore and upgrade rollback are repeatable; approved product ownership and operating boundaries are clear, while unresolved commercial decisions are explicitly recorded.

### One-shot Sol High implementation prompt

```text
Implement Phase 6 as the sale-readiness slice for a dedicated hosted pilot. Inspect the existing envelopes, project/auth boundary, JobRun/audit/metrics/console, Docker, Alembic, and provider/storage configuration; create a short internal execution plan before editing. Reuse them to add the narrow versioned HMAC webhook capability, stable OpenAPI/integration examples, the supported dedicated-deployment profile, and repeatable backup/restore/upgrade/rollback/reindex/key-rotation/incident/support-diagnostic workflows. Validate successful integration plus invalid signature, duplicate event, delivery retry/failure/replay, migration/upgrade failure, restore verification, and rollback paths. Complete the vertical slice, run exact relevant release checks, and provide concrete completion-gate evidence. Do not invent or commit to licensing, SLOs, pricing, service limits, support tiers, or commercial promises: identify unresolved decisions clearly and implement only choices already approved in repository/product direction. Do not add billing, public SaaS tenancy, customer RBAC, connectors, Kubernetes, or self-hosted packaging.
```

---

## Future F1 — Supported Self-Hosted Edition

**Readiness:** Future delivery model — after Phase 6 and a qualified need.

### Purpose

Package the proven dedicated-hosted core for customer-operated infrastructure without creating a product fork.

### Scope when justified by a signed customer need

- Reuse the same application images, migrations, configuration validation, console, backup/restore, diagnostics, and supported provider/storage matrix.
- Create a versioned release bundle, supported topology, preflight diagnostics, offline-friendly configuration templates, upgrade/rollback tooling, support bundle, compatibility matrix, and explicit customer/vendor responsibilities.
- Add Helm only when a contracted customer explicitly requires Kubernetes; retain the supported container profile otherwise.

### Completion gate

A customer can install, upgrade, diagnose, back up, and restore a supported release without vendor infrastructure access, while support can work from versioned diagnostics and a clear responsibility boundary.

### One-shot Sol High implementation prompt

```text
Implement Future F1 only after Phase 6 and a qualified customer requirement. Inspect and reuse the exact dedicated-hosted images, migrations, configuration validation, operator console, backup/restore, diagnostics, and supported matrix; make a short internal execution plan before editing. Package rather than fork the product. Build the narrow release, preflight, upgrade/rollback, support-bundle, compatibility, and responsibility features required by the approved topology. Validate clean install, upgrade from the prior supported version, backup/restore, diagnostics, and unsupported-configuration failure paths. Run exact checks and report evidence. Do not add a public SaaS control plane, broad infrastructure support, new providers, or customer-specific authorization.
```

---

## Future F2 — Customer-Specific Authorization

**Readiness:** Future / real customer demand only.

### Purpose

Add entitlement-driven metadata filtering only when one corpus must serve multiple permission levels; retain one project per hard authorization boundary until then.

### Scope when justified by a signed customer need

- Accept a signed, validated, allowlisted entitlement context from the host application. The service remains machine-to-machine and does not become the source of user identity.
- Enforce access predicates in semantic SQL, keyword retrieval, hybrid fusion, rerank hydration, citations, conversations, and document APIs, with privacy-conscious audit records.
- Add SSO, SCIM, RBAC, or a general policy engine only when an explicit customer administration contract requires it.

### Completion gate

Two users querying the same project receive only evidence allowed by their signed entitlement context, and consistent enforcement is verified across search, chat, citations, and document APIs.

### One-shot Sol High implementation prompt

```text
Implement Future F2 only for a confirmed customer requirement after Phase 6. Inspect project scoping, existing allowlisted metadata filters, search/hybrid/rerank/citation/conversation/document paths, and audit boundaries; create a short internal execution plan before editing. Preserve organization API keys and project scoping as the outer tenant boundary. Add only the signed host-supplied entitlement context and the minimum consistent enforcement required. Validate cross-user/project leakage, filter bypass, citation leakage, missing/invalid entitlement, and normal access paths. Run exact checks and report evidence. Do not introduce a general policy engine, identity provider, SSO, SCIM, RBAC, or ABAC framework unless the signed customer scope explicitly requires it.
```

---

## Final product cut line

Phase 3 is the practical personal-use destination. Phase 4 is the trust-quality threshold. Phase 5 is production-ready operation. Phase 6 is the minimum sale-ready cut for a dedicated hosted pilot. Self-hosting and customer-specific authorization remain future, demand-led work.
