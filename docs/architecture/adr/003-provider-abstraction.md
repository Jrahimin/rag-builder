# ADR-003: Provider Abstraction Layer

**Status:** Accepted (refined)  
**Date:** 2026-06-28

## Context

APE must support multiple LLM, vector DB, storage, and OCR vendors without
rewriting business logic. Vendor SDK types must not leak into services.

## Decision

- Provider **interfaces** are added in `app/platform/providers/` when the first
  implementation ships — not as speculative ABCs upfront.
- Vendor SDKs are confined to `platform/providers/implementations/`.
- Connectivity adapters (Redis, Qdrant health) live in
  `platform/infra/connectivity/` — not in general DI.
- `ProviderError` taxonomy in `platform/providers/errors.py`.
- `ProviderCapability` reference enum in `contracts.py`.

## Consequences

- Less boilerplate before Phase 1 features; interfaces grow with real consumers
- Contract tests added alongside first implementation
- Switching vendors still requires replacing one implementation package

## Alternatives considered

| Alternative | Rejected because |
| ----------- | ---------------- |
| Direct SDK in services | Vendor lock-in; untestable |
| Full interface catalog upfront | Speculative; no consumers yet |
| Providers in feature modules | Infrastructure is platform concern |
