# Provider Architecture

> Canonical layout: [module-architecture.md](./module-architecture.md)

## Rules

1. Business code uses provider **interfaces** (added with first implementation).
2. Vendor SDKs stay in `platform/providers/implementations/`.
3. `ProviderError` taxonomy in `platform/providers/errors.py`.
4. **Connectivity** for external services is `platform/infra/connectivity/` — not general DI.

## What exists today

- `ProviderCapability` reference enum (`providers/contracts.py`)
- `ProviderError` hierarchy
- **Embeddings** — `BaseEmbeddingProvider` + Ollama / OpenAI / Gemini / hash implementations
- **Semantic persistence** — retrieval-owned pgvector repository; it is not a
  model-facing provider. There is no vector-store provider or external vector client.
- **Storage** — `BaseStorageProvider` + local / MinIO implementations
- **Document parsers** — `BaseDocumentParserProvider` + PyMuPDF / plain text / docx / image OCR
- **OCR** — `OCRProvider` + optional PaddleOCR (`ocr_factory.py`); SDK boundary same as other providers. **Bangla (`bn`) is not supported** on the Paddle backend in Phase 1 — see [multilingual_support.md](../features/multilingual_support.md#known-limitation-bangla-bengali-ocr).
- **LLM** — `BaseLLMProvider` + echo / OpenAI-compatible / Ollama / Gemini implementations (Chat module)

## SDK boundary

```text
Module service → provider interface → implementation → vendor SDK
```

Forbidden: vendor SDK objects in modules or `dependencies/`. PostgreSQL-specific
vector expressions stay inside the retrieval repository.

## LLM integration pattern (provider-agnostic)

All consumers use the **same call surface** regardless of backend:

```text
get_llm_provider()  →  BaseLLMProvider
  .generate(messages, temperature=..., max_tokens=...)  → ChatCompletionResult
  .stream(messages, ...)  → AsyncIterator[ChatCompletionChunk]
```

Switch backend via `APE_LLM__BACKEND` only — never import vendor implementations from modules. Full guide: [conversation_provider_integration.md](../learning/conversation_provider_integration.md).

Factories are called by composition (`dependencies/`, `composition/`, worker
handlers, or operational CLIs). Services receive provider contracts or a typed
resolver; they do not select concrete provider implementations.

Same pattern for embeddings (`get_embedding_provider`) and storage
(`get_storage_provider`). Semantic search uses the session-aware retrieval
repository defined by ADR-013.
