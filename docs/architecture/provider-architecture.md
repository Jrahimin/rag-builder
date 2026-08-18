# Provider Architecture

> Canonical layout: [module-architecture.md](./module-architecture.md)

## Rules

1. Business code uses provider **interfaces** (added with first implementation).
2. Vendor SDKs stay in `platform/providers/implementations/`.
3. `ProviderError` taxonomy in `platform/providers/errors.py`.
4. **Connectivity** for external services is `platform/infra/connectivity/` — not general DI.

## Certified production matrix

Certified production profiles:

| Runtime profile | LLM | Embeddings |
| --- | --- | --- |
| `hosted_managed` (preferred) | OpenAI `gpt-5.6-luna` | Cohere `embed-v4.0` |
| `hosted_openai` (deprecated compatibility) | OpenAI-compatible route through the `openai` adapter | `openai` |
| `private_ollama` | `ollama` | `ollama` |

`hosted_openai` must still start without Cohere. Rerank and query-translation availability must
not block API startup; missing Cohere **embed** configuration fails `hosted_managed` validation.

Other implemented adapters remain available for development/comparison but are non-certified in
production. Startup invokes each configured capability once under a timeout and caches the result;
readiness never repeats model calls.

## What exists today

- `ProviderCapability` reference enum (`providers/contracts.py`)
- `ProviderError` hierarchy
- **Embeddings** — `BaseEmbeddingProvider` + Cohere / Ollama / OpenAI / Gemini / hash implementations.
  Call sites pass vendor-neutral `QUERY` or `DOCUMENT`; only Cohere maps those to `search_query` /
  `search_document`.
- **Semantic persistence** — retrieval-owned pgvector repository; it is not a
  model-facing provider. There is no vector-store provider or external vector client.
- **Storage** — `BaseStorageProvider` + local / MinIO implementations
- **Document parsers** — `BaseDocumentParserProvider` + PyMuPDF / plain text / docx / image OCR
- **OCR** — `OCRProvider` + optional PaddleOCR for the general fallback and Google Cloud Vision for the opt-in Bangla OCR-first route (`ocr_factory.py`); SDK boundary same as other providers. See [multilingual_support.md](../features/multilingual_support.md#bangla-bengali-ocr-routing-and-limitations).
- **LLM** — `BaseLLMProvider` + echo / OpenAI-compatible / Ollama / Gemini implementations (Chat module)
- **LLM capabilities** — versioned provider/model descriptors centralize supported parameters,
  ranges, safe omission, and vendor token-limit names (`max_completion_tokens`,
  `maxOutputTokens`, or `num_predict`). Effective Project policy is validated before a request is
  staged or sent.

Capability-gated wire behavior remains adapter-specific. Streaming usage options are sent only
when the selected descriptor supports them; a generic OpenAI-compatible endpoint is not assumed
to accept OpenAI's `stream_options.include_usage`. For Ollama, `num_predict` is the maximum output
budget and is checked against the configured model's `/api/show` context information (including a
lower `num_ctx` setting) before a request is sent. APE rejects an over-limit request rather than
silently truncating it.

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

Unsupported explicit parameters fail with `unsupported_provider_parameter`; out-of-range values
fail with `provider_parameter_out_of_range`. A value inherited from deployment defaults may be
safely omitted only when the descriptor explicitly marks it unsupported. The descriptor version
is persisted in execution provenance.
