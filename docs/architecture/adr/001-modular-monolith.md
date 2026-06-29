# ADR-001: Modular Monolith with Platform Kernel

**Status:** Accepted  
**Date:** 2026-06-28

## Context

The foundation initially used global technical layers (`api/`, `services/`,
`repositories/`, `providers/`) at the application root while `architecture.mdc`
also described a `modules/` feature structure. This created ambiguity about
where new code belongs and how dependencies flow.

## Decision

Adopt a **modular monolith** with four top-level packages:

1. **`core/`** — cross-cutting concerns (config, logging, exceptions)
2. **`platform/`** — shared technical kernel (db, providers, jobs, http envelopes)
3. **`modules/`** — feature vertical slices (business capabilities)
4. **`api/`** — composition root (HTTP mounting only)

Layers (Router → Service → Repository/Provider) exist **inside each module**,
not as global root packages.

## Consequences

- Clear ownership: business code lives in `modules/<feature>/`
- Platform code is reusable without coupling to features
- Dependency rules are enforceable (modules cannot import each other’s internals)
- Slight migration cost when adding the first feature module

## Alternatives considered

| Alternative | Rejected because |
| ----------- | ---------------- |
| Global layers only | Does not scale; features sprawl across directories |
| Microservices per feature | Premature; violates self-hosted single-deployment model |
| `modules/` containing `providers/` | Confuses infrastructure with business; providers are platform concern |
