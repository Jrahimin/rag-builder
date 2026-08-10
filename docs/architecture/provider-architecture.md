# Provider Architecture

> Canonical layout: [module-architecture.md](./module-architecture.md)

## Rules

1. Business code uses provider **interfaces** (added with first implementation).
2. Vendor SDKs stay in `platform/providers/implementations/`.
3. `ProviderError` taxonomy in `platform/providers/errors.py`.
4. **Connectivity** for external services is `platform/infra/connectivity/` — not general DI.

## Certified production matrix

Only two combinations are startup-certified:

| Runtime profile | LLM | Embeddings |
| --- | --- | --- |
| `hosted_openai` | OpenAI-compatible route through the `openai` adapter | `openai` |
| `private_ollama` | `ollama` | `ollama` |

Other implemented adapters remain available for development/comparison but are non-certified in
production. Startup invokes each configured capability once under a timeout and caches the result;
readiness never repeats model calls.

## What exists today

- `ProviderCapability` reference enum (`providers/contracts.py`)
- `ProviderError` hierarchy
- **Embeddings** — `BaseEmbeddingProvider` + Ollama / OpenAI / Gemini / hash implementations
- **Semantic persistence** — retrieval-owned pgvector repository; it is not a
  model-facing provider. There is no vector-store provider or external vector client.
- **Storage** — `BaseStorageProvider` + local / MinIO implementations
- **Document parsers** — `BaseDocumentParserProvider` + PyMuPDF / plain text / docx / image OCR
- **OCR** — `OCRProvider` + optional PaddleOCR for the general fallback and Google Cloud Vision for the opt-in Bangla OCR-first route (`ocr_factory.py`); SDK boundary same as other providers. See [multilingual_support.md](../features/multilingual_support.md#bangla-bengali-ocr-routing-and-limitations).
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
