# Embeddings Fundamentals

Dense vectors represent text meaning in a fixed-dimensional space. Similar texts produce vectors with high cosine similarity, enabling semantic retrieval without exact keyword matches.

## How APE stores embeddings (Phase 1)

- Vectors are packed as **float32 BYTEA** in PostgreSQL (`chunk_embeddings`)
- Metadata captures `provider`, `model`, `dimensions`, `embedding_set_version`, and content hash
- **`embedding_set_version`** is independent of `Document.version` — bump it to re-embed all documents after a model change

## Provider abstraction

`BaseEmbeddingProvider` hides vendor APIs. Implementations:

| Backend | Use case |
| ------- | -------- |
| `hash` | Local dev / tests (deterministic pseudo-vectors) |
| `ollama` | Self-hosted models |
| `openai` | OpenAI embeddings API (any OpenAI-compatible host via `APE_EMBEDDING__OPENAI_BASE_URL`) |
| `gemini` | Google Gemini `batchEmbedContents` API |

## Request flow (embed)

```text
POST /embed → IndexingService.enqueue_embed → document.embed worker
  → EmbeddingWorkflow → chunk_embeddings rows → status=embedded
```

## Trade-offs

| Choice | Why |
| ------ | --- |
| BYTEA vs pgvector | Simpler Phase 1; pgvector optional later |
| PG + Qdrant | PG is source of truth; Qdrant is search-optimized replica |
| Worker handoff vs workflow hook | Preserves module boundary (knowledge never imports retrieval) |
