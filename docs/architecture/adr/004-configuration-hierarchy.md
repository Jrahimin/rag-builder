# ADR-004: Three-Tier Configuration Model

**Status:** Accepted (refined)  
**Date:** 2026-06-28

## Context

AI settings (models, chunk size, retrieval params) must be configurable at
deployment and per-Project levels without hardcoding.

## Decision

Three configuration tiers with explicit precedence:

```text
deployment (env) → platform (DB) → project (DB)
```

- **Deployment:** `core.config.Settings` via `APE_*` environment variables (implemented)
- **Platform / Project:** deferred — `ConfigLayer` + `CONFIG_PRECEDENCE_ORDER` in
  `platform/config/contracts.py` document the model only

A typed `ConfigResolver` is introduced with the Projects module (first consumer).

## Consequences

- Foundation uses env-only config; Project overrides added without restructuring
- No generic key/value resolver until a real schema exists
- Requires versioned Project config storage (Phase 1)

## Alternatives considered

| Alternative | Rejected because |
| ----------- | ---------------- |
| Env-only forever | Cannot support per-Project AI settings |
| Generic resolver upfront | No consumer; speculative abstraction |
| Hardcoded defaults in services | Violates configuration-driven design |
