# ADR-016: Transactional outcome webhooks and one dedicated hosted profile

**Status:** Accepted  
**Date:** 2026-07-19

## Context

Phase 0–5 produced an operable RAG service, but a dedicated customer pilot still
needed a reliable outbound completion contract and one repeatable delivery and
recovery topology. A generic event bus, Kubernetes packaging, or commercial policy
engine would expand the product beyond the approved dedicated-hosted cut.

## Decision

- Publish only versioned document processing/indexing success/failure events.
- Stage an immutable event and subscribed deliveries in the same PostgreSQL
  transaction as the terminal `JobRun` transition.
- Deliver canonical JSON through a separate leased database dispatcher. Sign each
  request with an endpoint-specific HMAC secret derived from a deployment master
  key; retain bounded attempt records and preserve the event ID on replay.
- Keep endpoint configuration and inspection Project-scoped. A disabled endpoint
  stops new claims without deleting history.
- Support one operator-managed, single-host dedicated Compose profile with
  digest-pinned images, TLS ingress, private data services, explicit egress,
  migration gating, probes, limits, workers, console, PostgreSQL/pgvector, Redis,
  MinIO, and ClamAV.
- Keep backup, restore, upgrade, rollback, diagnostics, and destructive execution in
  guarded operator tooling. The console exposes safe status and replay/disablement,
  not infrastructure mutation.

## Consequences

- A terminal job outcome and its initial integration intent cannot commit separately.
- Receiver deduplication has a stable event ID and signature contract; delivery can
  recover from process crashes and non-2xx responses without a second queue system.
- Rotating the deployment webhook key changes all derived endpoint secrets, so the
  runbook requires coordinated endpoint recreation.
- The supported topology is intentionally narrow. Self-hosted packaging,
  Kubernetes, billing, licensing, SLOs, and customer authorization remain explicit
  future/commercial decisions.

## Alternatives considered

- **Publish HTTP inline from the job:** rejected because receiver latency/failure
  would couple customer availability to the job transaction.
- **Reuse Taskiq for webhook delivery:** rejected because PostgreSQL already owns the
  audit/retry state and a second dispatch intent would complicate atomicity.
- **Store endpoint plaintext secrets:** rejected because deterministic per-endpoint
  derivation avoids another secret-at-rest lifecycle.
- **Add Kubernetes or a public control plane:** rejected as later-phase scope without
  an approved customer need.
