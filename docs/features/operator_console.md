# Operator Console and Test Lab

## Purpose

The responsive React console operates and validates one trusted dedicated deployment without routine terminal, database, or Postman access. It is mounted at `/operator/`; the browser-based end-to-end verification surface is `/operator/lab`.

The Test Lab is one connected product-testing workflow, not an admin/developer split or a generic test platform. It adds no authentication, RBAC, customer administration, billing, connector management, or separate frontend application.

## Architecture

```text
React feature screens
        ↓ TanStack Query (cache, polling, invalidation)
typed operatorApiClient
        ↓ API envelopes + generated OpenAPI types
existing operator and project-scoped FastAPI APIs
        ↓
durable jobs, immutable index builds, retrieval, grounded conversations
```

Feature components never call `fetch`. `operatorApiClient.ts` owns multipart upload, JSON requests, envelope decoding, stable backend error codes, trace IDs, and backend-unavailable errors. The Lab stores only its current activity timeline in frontend state; it does not add a backend test-run model.

## Routes

| Route                     | Purpose                                          |
| ------------------------- | ------------------------------------------------ |
| `/operator/`              | Deployment overview                              |
| `/operator/lab`           | Test Lab journey and direct testing tabs         |
| `/operator/jobs`          | Project/all-project durable job list and details |
| `/operator/projects`      | Project, document, and corpus inspection         |
| `/operator/configuration` | Sanitized active configuration                   |
| `/operator/metrics`       | Queue, latency, usage, and corpus measurements   |
| `/operator/quality`       | Versioned evidence-quality results               |
| `/operator/audit`         | Immutable operator and durable-job events        |
| `/operator/health`        | Dependencies and worker heartbeats               |

Job deep links accept `project` and `job` query parameters. The Test Lab persists `project` and `tab` in its query string, so a selected project and direct tab survive refresh and can be shared.

## Test Lab journey

The persistent Lab header contains the primary project selector, an ordinary **Create test project** action, a compact document/job/build/conversation session summary, and the Activity drawer button.

The default Journey tab derives five states from actual project data and the current browser session:

1. Select project.
2. Upload and process a document.
3. Verify search.
4. Verify a grounded message and citations, or a valid insufficient-evidence refusal.
5. Refresh or change the corpus.

Journey status never treats an accepted request as completed work. Document and lifecycle requests first show **Request accepted**, then follow the returned durable `job_id`; only a terminal job or authoritative build/document state can pass a processing step.

## Direct tabs

### Documents

- Drag/drop and picker upload for the backend-supported PDF, DOCX, text/Markdown, and image formats.
- Real document status, version, parser, page/language facts, error detail, and related durable jobs.
- State-guarded Reprocess, Embed, Index, Delete, and Purge actions.
- Delete explains retained corpus-level reversibility. Purge requires the exact filename; browser confirmation alone is not accepted.
- Every accepted action links to the generated job and distinguishes running, succeeded, and failed outcomes.

### Search

- One query, optional expected-word assertion, and collapsed safe document/strategy filters.
- Ranked real hits with score, filename, page/source offsets, excerpt, chunk identifier, request/backend timing, and the active build captured for that run.
- No results are a valid outcome. Expected words pass only when at least one returned chunk contains the entered phrase.

### Messages

- Creates a normal project conversation on demand and uses the non-streaming grounded-message API.
- Shows persisted history, answer timing, explicit insufficient-evidence reasons, and durable citation snapshots with document, page/source metadata, score, and excerpt or stable chunk reference.
- An answer passes grounding only with `grounded=true` and citations, or as an explicit valid refusal. Non-empty answer text alone never passes.
- Missing active corpus state links directly to Documents and Lifecycle.

### Lifecycle

- Re-embed whole corpus, Reindex whole corpus, Activate validated build, Rollback, and Reconcile storage reuse the existing Phase 5 lifecycle component.
- Active and previous pointers, build state/operation/counts/times, validation readiness, and failure details are visible together.
- Activation is enabled only for validated/retained builds. Rollback names the exact build that will become active.
- Lifecycle jobs appear immediately and remain linked to full Jobs details. Reconciliation `expected`, `actual`, `missing`, `orphan`, and `consistent` results render as a structured report.

The Jobs type filter includes `corpus.reembed`, `corpus.reindex`, `document.delete`, `document.purge`, and `storage.reconcile`.

## Activity drawer

The desktop right drawer becomes a full-width mobile sheet. Newest actions appear first with accepted/running/passed/failed/warning outcome, relevant project/document/job/build/conversation identifiers, stable backend error code and trace ID, and deep links to Jobs, Documents, Lifecycle, and Audit.

Raw JSON is not the default presentation. Structured facts and friendly outcomes lead; a collapsed Technical details section exposes identifiers and structured result data. A compact session summary is copyable.

## Query, error, and polling behavior

- Job lists poll every 3 seconds while active and every 15 seconds otherwise; an opened job detail polls every 2 seconds while active.
- Documents and builds refetch after relevant mutations; projects, jobs, audit, and conversations are invalidated through shared TanStack Query keys.
- Backend envelope failures retain stable error `code` and `trace_id`. The UI separately identifies backend-unavailable failures.
- Loading, empty, no-results, insufficient-evidence, failure, and unavailable states are all explicit.

## Testing and intentional limits

Vitest and Testing Library cover Lab routing/project selection, Journey derivation, accepted versus terminal uploads, expected-word pass/fail, grounded citations and refusal, lifecycle job links and active-pointer change, structured reconciliation, typed purge confirmation, and error code/trace visibility. Existing overview, jobs, health, quality, and lifecycle tests remain in place.

The console remains an internal trusted-deployment surface. It does not add login, sessions, cookies, users, browser-stored credentials, RBAC, a generic test-run backend, long-term Lab-session persistence, customer-facing chat, billing, connectors, or a second admin/developer console.
