# Configuration Architecture

> Canonical layout: [module-architecture.md](./module-architecture.md)

## Layers (precedence, lowest → highest)

```text
1. Deployment  — core.config.Settings (APE_* env vars) ✅ active
2. Platform    — DB-backed defaults (future)
3. Project     — per-Project overrides (future)
```

`ConfigLayer` + `CONFIG_PRECEDENCE_ORDER` in `platform/config/contracts.py`.

## What is deferred

- Generic key/value `ConfigResolver`
- Typed Project configuration schemas

Introduce a typed resolver with the Projects module (first consumer).

## Rules

- Nothing AI-related hardcoded in services
- Project overrides cannot weaken deployment security
- Configuration changes are versioned and audited (Phase 1+)
