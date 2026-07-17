# Configuration Architecture

> Canonical layout: [module-architecture.md](./module-architecture.md)

## Layers (precedence, lowest → highest)

```text
1. Deployment  — core.config.Settings (APE_* env vars) ✅ active
2. Platform    — DB-backed defaults (future)
3. Project     — per-Project overrides (future)
```

`ConfigLayer` + `CONFIG_PRECEDENCE_ORDER` in `platform/config/contracts.py`.

## Durable job snapshots

Every durable ingestion/indexing job references an immutable,
Project-scoped `JobConfigurationSnapshot`. The snapshot is normalized and
content-addressed by SHA-256, includes the parsing/chunking/OCR/embedding/
retrieval values that determine outputs, and deliberately excludes credentials.
Workers combine that snapshot with live deployment secrets. Retries therefore
reproduce the original processing choices without persisting secret material.

This is execution provenance, not a generic configuration override system.

## What is deferred

- Generic key/value `ConfigResolver`
- Typed Project configuration schemas

Introduce a typed resolver with the Projects module (first consumer).

## Rules

- Nothing AI-related hardcoded in services
- Project overrides cannot weaken deployment security
- Output-affecting job configuration is immutable and hash-addressed; generic
  configuration changes and audit history remain deferred
- API and worker composition pass one explicit `Settings` snapshot when wiring a
  service. Provider/config selection does not live inside module services.
