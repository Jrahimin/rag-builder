# Hosted Integration and Commercial Delivery

## Purpose

Phase 6 turns the Phase 0–5 product into a repeatable operator-managed dedicated
pilot: customer applications receive signed outcomes, the v1 contract is explicit,
and operators use one supported deployment and recovery path.

## Webhook architecture

```text
JobService terminal transaction
  -> immutable WebhookEvent + subscribed WebhookDelivery rows
  -> commit
  -> leased WebhookDispatcher
  -> canonical JSON + HMAC-SHA256
  -> attempt history / backoff / terminal failure
```

`modules/webhooks/` owns configuration, inspection, replay, and delivery state.
`platform/webhooks/` owns producer/transport contracts and signing primitives.
`composition/webhooks.py` connects terminal jobs to persistence and runs the dispatcher.
HTTP delivery stays behind `WebhookTransport`; PostgreSQL is the delivery source of truth.

Only four event types exist: processing/indexing success/failure v1. This is not a
general event bus. Endpoint secrets are derived from the deployment signing key and
endpoint UUID, returned on creation, and never stored as endpoint plaintext.

## Correctness and failure behavior

- Event source keys are unique per Project and terminal job outcome.
- Initial delivery and event rows commit atomically with the terminal job transition.
- A delivery lease recovers dispatcher crashes. Non-2xx responses and transport failures
  back off exponentially up to the configured attempt limit.
- Every completed HTTP attempt is immutable and operator-visible; responses are bounded.
- Replay creates a new delivery but preserves the event ID for receiver deduplication.
- Disabled endpoints are not claimed; history remains intact.
- Production endpoint URLs require HTTPS and startup requires a non-default signing key.

## Configuration

| Variable | Development default | Production |
| --- | --- | --- |
| `APE_WEBHOOKS__ENABLED` | `true` | required `true` |
| `APE_WEBHOOKS__DISPATCHER_ENABLED` | `true` | required `true` |
| `APE_WEBHOOKS__SIGNING_KEY` | development-only | unique 32+ byte secret |
| `APE_WEBHOOKS__MAX_ATTEMPTS` | `6` | deployment-approved |
| `APE_WEBHOOKS__RETRY_BASE_SECONDS` | `5` | deployment-approved |
| `APE_WEBHOOKS__RETRY_MAX_SECONDS` | `3600` | deployment-approved |
| `APE_WEBHOOKS__DELIVERY_TIMEOUT_SECONDS` | `10` | deployment-approved |

## Hosted profile

`infra/hosted/compose.yaml` is the supported single-host dedicated pilot topology. It
uses digest-pinned release images, TLS ingress, a private data network, explicit egress,
migration gating, health probes, resource limits, PostgreSQL/pgvector, Redis, MinIO,
ClamAV, API, workers, and console. It is operator-managed, not self-hosted packaging.

`infra/hosted/hostedctl.py` validates immutable release inputs and provides guarded,
repeatable backup, restore, upgrade, rollback, and redacted diagnostic flows. See the
[hosted operations runbook](../../infra/hosted/RUNBOOK.md).

## Testing strategy

Unit coverage verifies signatures, tamper rejection, immutable-image guards, and
destructive confirmation. PostgreSQL integration coverage verifies job-outcome
publication, non-2xx retry, attempt history, signature verification, disablement, and
same-event-ID replay. Frontend checks compile inspection UI from generated OpenAPI.

## Test Phase 6 locally without Docker

Keep the already-tested local PostgreSQL, Redis, API, and worker setup. Add only:

```dotenv
APE_WEBHOOKS__ENABLED=true
APE_WEBHOOKS__DISPATCHER_ENABLED=true
APE_WEBHOOKS__SIGNING_KEY=local-phase6-signing-key-at-least-32-bytes
```

Run `alembic upgrade head`, restart the local API, create an HTTP endpoint at
`http://127.0.0.1:9010/webhooks/ape` (HTTP is allowed outside production), copy its
returned `signing_secret`, then start:

```powershell
python scripts/webhook_receiver.py --secret "<returned-signing-secret>"
```

Upload/process a document. The receiver prints the signed v1 event. Stop the receiver,
process another document, inspect retry history in `/operator/webhooks`, restart it, and
replay the failed delivery. No Docker-specific change is required for this phase-only test.

## Intentional limits

No billing, public SaaS tenancy, licensing commitment, customer RBAC, connectors,
Kubernetes, self-hosted release, or general event platform is included. Open commercial
decisions are recorded in `docs/operations/commercial-decisions.md`.

## Phase 0–6 alignment audit

| Phase | Roadmap gate evidence | Result |
| --- | --- | --- |
| 0 | Canonical module/domain/runtime flow docs plus import-boundary tests match the modular monolith | aligned |
| 1 | PostgreSQL `JobRun`/outbox/leases/retries and Project-scoped job inspection remain the only durable job path | aligned |
| 2 | Production startup rejects fake/incompatible providers; health, readiness, metrics, worker and configuration status are stable APIs | aligned |
| 3 | The private console covers health, documents, jobs, configuration, metrics, audit, safe retries, and webhook inspection | aligned |
| 4 | Reproducible evaluations, hybrid/reranked retrieval, persisted claim evidence, grounded answers, and refusal remain exposed | aligned |
| 5 | Immutable builds, atomic activation/rollback, job-backed corpus changes, upload security, deletion, and reconciliation remain intact | aligned |
| 6 | Transactional signed webhooks, stable v1 contracts, the dedicated profile, guarded recovery tooling, onboarding boundaries, and open commercial decisions complete the pilot cut | aligned |

No Phase 6 implementation introduces Future F1 self-hosted packaging or Future F2
customer-specific authorization. The phase sequence remains one cumulative path rather
than parallel legacy/new flows.
