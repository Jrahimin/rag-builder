# Learn RAG by Following the Data

> **Start here:** [RAG from Zero](./rag-from-zero.md)

APE is built as a learning journey as much as an AI backend. The learning docs are designed for a beginner who wants to understand both the idea and the engineering that makes it dependable.

You will follow one question from a raw PDF to a grounded answer. Each chapter explains:

- the plain-language idea;
- the decision the engineer is making;
- the path through this repository;
- the configuration knobs that change the behavior;
- a small experiment or checkpoint to make the concept stick.

## The recommended path

### 1. See the whole story first

Read [RAG from Zero](./rag-from-zero.md). It gives you the mental model without requiring you to know FastAPI, vectors, or LLM internals.

### 2. Watch a document enter the engine

Read these in order:

1. [Knowledge Ingestion — End to End](./knowledge-ingestion-journey.md)
2. [Object Storage for RAG](./object-storage-for-rag.md)
3. [Document Parsing and Extraction](./document-parsing-and-extraction.md)
4. [OCR Fundamentals](./ocr-fundamentals.md)
5. [Text Chunking for RAG](./text-chunking-for-rag.md)
6. [Multilingual Text Processing](./multilingual-text-processing.md)

**Milestone:** you can explain why the API returns before parsing finishes, why a file has a raw and parsed representation, and why chunk boundaries affect answers.

### 3. Turn text into searchable meaning

1. [Embeddings Fundamentals](./embeddings-fundamentals.md)
2. [Vector Storage and pgvector](./vector-storage-and-pgvector.md)
3. [Semantic Search for RAG](./semantic-search-for-rag.md)
4. [Hybrid Retrieval Journey](./hybrid-retrieval-journey.md)
5. [pgvector Operations Runbook](./pgvector-operations-runbook.md)

**Milestone:** you can explain why semantic search and keyword search complement each other, and which setting you would change when a result is missing.

### 4. Turn evidence into a conversation

1. [Conversation RAG Journey](./conversation_rag_journey.md)
2. [RAG Prompting](./conversation_rag_prompting.md)
3. [Conversation Provider Integration](./conversation_provider_integration.md)
4. [Conversation LLM Providers](./conversation_llm_providers.md)
5. [Conversation Memory](./conversation_memory.md)
6. [SSE Streaming](./conversation_sse_streaming.md)

**Milestone:** you can trace a question through retrieval, context building, prompt construction, model generation, persistence, and citations.

### 5. Understand the platform underneath

When you are ready for the infrastructure story:

- [Configuration System](./configuration-system.md)
- [Application Factory and FastAPI](./application-factory-and-fastapi.md)
- [Database and Migrations](./database-and-migrations.md)
- [Entity Lifecycle and Reusability](./entity-lifecycle-and-reusability.md)
- [Organization API Key Auth Journey](./organization-api-key-auth-journey.md)
- [Structured Logging](./structured-logging.md)
- [Docker Local Development](./docker-local-development.md)
- [Testing Strategy](./testing-strategy.md)

## How to use each chapter

Do not read these documents like a glossary. Use the same loop each time:

1. **Predict:** what should happen next in the pipeline?
2. **Trace:** open the referenced source file and follow one request or worker call.
3. **Tweak:** change one configuration value or input condition.
4. **Observe:** compare the status, chunks, scores, prompt, or answer.
5. **Explain:** write one sentence about why the behavior changed.

That is the difference between recognising words such as “embedding” and actually understanding the system.

## Repository orientation

```text
backend/app/
  api/            HTTP routes and request contracts
  dependencies/   FastAPI dependency wiring and access checks
  modules/
    knowledge/    documents, parsing, chunking, lifecycle
    retrieval/    embeddings, keyword index, vector search, fusion
    conversations/ chat orchestration, prompts, citations
  platform/       providers, database, persistence, auth, jobs
  worker/         background task entrypoints

docs/
  architecture/  boundaries and decisions
  features/      behavior contracts
  learning/      why the system works this way
```

The learning docs point to implementation files intentionally. The goal is to make the jump from concept to code feel natural.

## Three questions to keep asking

### Where can information be lost?

During parsing, chunking, filtering, context trimming, or generation. A good RAG engineer finds the first point where the useful signal disappears.

### Which boundary owns this decision?

Knowledge owns document preparation. Retrieval owns evidence selection. Conversations owns prompts and model calls. The composition layer connects them.

### What would I measure?

Not just whether the API returned `200`. Measure extraction quality, retrieval recall, citation coverage, groundedness, latency, and cost.

## Platform overview

- [Platform at a Glance](../Platform-at-a-glance.md)
- [Platform Integration Guide](../platform-integration-guide.md)
- [Architecture overview](../architecture/README.md)
- [Features overview](../features/README.md)
