# Vector Storage and pgvector: Keep the Map Beside the Documents

> **The idea:** embeddings are the map; pgvector is where APE stores and searches that map without separating it from the business data that gives the result meaning.

APE uses PostgreSQL for:

- organizations, projects, and documents;
- chunk text and source metadata;
- keyword/BM25 rows;
- native vector values and nearest-neighbor indexes.

That choice keeps semantic search and relational filters in one source of truth.

## The stored shape

```text
document_chunks
    -> chunk_embeddings.embedding: vector(n)
    -> provider/model/version metadata
    -> HNSW cosine index
```

The `chunk_embeddings` record includes enough information to answer:

```text
Which chunk is this?
Which project owns it?
Which embedding provider/model created it?
Which dimension and embedding set does it belong to?
Which document/content version produced it?
```

## Why `vector(n)` is fixed

The database schema declares the dimension. If the embedding model changes from 384 dimensions to 1536, this is not a simple environment-variable edit:

1. migrate the vector column/index;
2. embed every active chunk again;
3. rebuild the keyword/index snapshot;
4. switch search to the new embedding set.

The explicit migration is a safety feature. It prevents incompatible vector spaces from quietly mixing.

## HNSW in plain language

An exact search compares the query with every vector. That is simple but slow as the corpus grows. HNSW builds a graph that lets the database jump through likely neighbors.

```text
exact: query -> compare with everything
HNSW:   query -> navigate likely neighbors -> compare a smaller set
```

The trade-off is approximate recall. More search effort can find better neighbors but costs more latency. `APE_RETRIEVAL__HNSW_EF_SEARCH` is one of the controls.

## Filters are part of the query

A nearest neighbor from another project is not a valid result. APE’s semantic SQL applies project, document lifecycle, provider/model/version, optional document, and allowlisted metadata filters before returning candidates.

This is why putting vectors beside relational data is valuable: the database can answer “nearest approved chunk in this project with this metadata,” not only “nearest chunk globally.”

Approximate indexes need special attention when filters are selective. Test filtered recall and tune HNSW/iterative-scan behavior as the corpus grows.

## The search path

```mermaid
flowchart LR
    Q[Question] --> E[Embed query]
    E --> V[pgvector cosine distance]
    V --> F[Project + lifecycle + metadata filters]
    F --> K[Chunk IDs and scores]
    K --> H[Hydrate content once]
```

## A hands-on experiment

Create a project with ten chunks and search the same query with different `top_k` and HNSW effort values. Compare:

- latency;
- returned chunk IDs;
- whether the expected chunk remains present;
- whether filters change the result set correctly.

Do not treat a faster query as better if it stops returning the evidence you need.

## Learning checkpoint

You understand pgvector when you can answer:

> Why is a vector database row not useful by itself without its project, document, version, and source metadata?

Next: [Semantic Search](./semantic-search-for-rag.md).

## Related

- [Embeddings Fundamentals](./embeddings-fundamentals.md)
- [Hybrid Retrieval Journey](./hybrid-retrieval-journey.md)
- [pgvector Operations Runbook](./pgvector-operations-runbook.md)
- `backend/app/modules/retrieval/repositories/chunk_embedding_repository.py`
