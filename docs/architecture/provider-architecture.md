# Provider Architecture

> Canonical layout: [module-architecture.md](./module-architecture.md)

## Rules

1. Business code uses provider **interfaces** (added with first implementation).
2. Vendor SDKs stay in `platform/providers/implementations/`.
3. `ProviderError` taxonomy in `platform/providers/errors.py`.
4. **Connectivity** (Redis, Qdrant health) is `platform/infra/connectivity/` — not general DI.

## What exists today

- `ProviderCapability` reference enum (`providers/contracts.py`)
- `ProviderError` hierarchy
- Empty `implementations/` package

Interfaces and neutral types are introduced when the first provider ships (likely
storage or vector store during knowledge ingestion).

## SDK boundary

```text
Module service → provider interface → implementation → vendor SDK
```

Forbidden: `Redis`, `AsyncQdrantClient`, `PointStruct`, etc. in modules or `dependencies/`.
