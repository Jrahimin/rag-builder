# Embeddings Fundamentals: Give Meaning a Shape

> **The idea:** a search engine needs a way to compare a question with passages that may use different words but express the same idea.

An embedding model maps text to a fixed-length vector:

```text
"refund within thirty days" -> [0.12, -0.07, 0.44, ...]
```

The individual coordinates are not labels such as “refund” or “policy.” Meaning is represented by the position of the whole vector in a high-dimensional space.

## Why keyword search is not enough

```text
Document: "Employees may request reimbursement within 30 days."
Question:  "How long do I have to get my money back?"
```

There may be no exact match for “get my money back.” Embeddings can place the question near a chunk about reimbursement because the concepts are related.

Keyword search is still valuable for exact identifiers. The best retrieval pipeline uses both; embeddings are one half of that conversation.

## The embedding flow in APE

```mermaid
flowchart LR
    C[Document chunks] --> P[Embedding provider]
    P --> V[vector(n) values]
    V --> DB[chunk_embeddings in PostgreSQL]
    Q[User question] --> P2[Same provider + model]
    P2 --> S[Cosine similarity search]
    DB --> S
    S --> H[Candidate chunks]
```

The main implementations live under `backend/app/platform/providers/implementations/`; orchestration lives under `backend/app/modules/retrieval/workflows/`.

## The most important rule: chunks and questions must share a space

Documents are embedded with a provider/model configuration. At query time, the question must use a compatible configuration.

```text
document chunks -> embedding model A -> vectors in space A
question        -> embedding model A -> query vector in space A
```

If documents use model A and queries use model B, cosine distance is still calculable, but the comparison may be meaningless. APE records provider, model, dimensions, content hash, and `embedding_set_version` to make the active representation explicit.

## Why dimensions are a schema decision

The vector column is `vector(n)`. The value of `n` is not a cosmetic setting:

- larger dimensions can preserve more representation capacity but cost more storage and compute;
- changing dimensions requires a migration and re-embedding;
- old and new vector sets must not be mixed accidentally.

This is why the initial hosted product should support one certified embedding configuration per deployment. Flexibility is useful only when the migration story is safe.

## Provider choices in this repository

| Backend | Good for | Warning |
| --- | --- | --- |
| `hash` | Deterministic local tests and demos | Not semantic search; never use it to judge product quality |
| `openai` | Hosted embeddings | Requires provider credentials and cost control |
| `openai_compatible` | A compatible hosted or private endpoint | The endpoint/model must produce compatible dimensions and quality |
| `ollama` | Local or private deployment | More operational responsibility and model-management work |
| `gemini` | Google-hosted embeddings | Provider-specific credentials and limits |

The factory hides provider calls from retrieval workflows. That is a useful abstraction boundary; it is not a reason to advertise every provider as equally supported.

## Versioning is a safety belt

`Document.version` answers: “which source/parse revision is this?”

`embedding_set_version` answers: “which embedding representation indexed this chunk?”

Keep them separate:

```text
document v2 + embedding set 3 -> active search snapshot
```

When a model changes, create a new embedding set, re-embed chunks, rebuild keyword/index data, and only then mark the new snapshot ready.

## A hands-on experiment

Use five chunks and three questions:

1. one exact phrase;
2. one paraphrase;
3. one unrelated question.

Compare cosine scores with a real embedding backend and with `hash`. The point is not that hash vectors are “bad code.” They are deterministic scaffolding, useful for testing pipeline mechanics but not for measuring semantic quality.

## What to tune first

- **Embedding model:** changes the meaning representation and language/domain quality.
- **Dimensions:** changes storage/schema compatibility; do not tune casually.
- **Batch size:** changes provider throughput and memory pressure.
- **Candidate count:** changes how many chunks reach fusion/reranking.
- **Version:** changes reproducibility and reindexing behavior.

Do not tune the LLM first if the right chunk never reaches it.

## Learning checkpoint

You understand embeddings when you can explain:

> Why can two semantically similar sentences be found even when they share few words, and why must the question and document chunks use a compatible embedding space?

Next: [Vector Storage and pgvector](./vector-storage-and-pgvector.md), then [Semantic Search](./semantic-search-for-rag.md).
