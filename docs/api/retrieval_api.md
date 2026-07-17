# Retrieval API

Hybrid and semantic search plus indexing endpoints. Requires documents at `status=ready`.

**Prefix:** `/api/v1/projects/{project_id}`

> `/documents/{document_id}/embed` and `/documents/{document_id}/index` are
> mounted on the documents router for URL consistency but are owned by the
> **retrieval** module.

Search results are filtered to the deployment's active `embedding_set_version`
(`APE_RETRIEVAL__EMBEDDING_SET_VERSION`) so vectors and keyword rows from prior
embedding runs are excluded.

Embedding persists native pgvector rows. Indexing (`POST .../index`) refreshes
the PostgreSQL keyword index while retaining the existing lifecycle contract.

## POST `/documents/{document_id}/embed`

Enqueue embedding for a `chunked` document.

**Response:** existing Document shape with `data.status=embedding` and additive
`data.job_id`; inspect the run through the [Jobs API](./jobs_api.md).

## POST `/documents/{document_id}/index`

Enqueue retrieval-index finalization for an `embedded` document. The worker
validates the current native embedding set, rebuilds keyword/BM25 rows, and
marks the document ready in one PostgreSQL transaction.

**Response:** existing Document shape with `data.status=indexing` and additive
`data.job_id`; the worker eventually moves the Document to `ready`.

## POST `/search`

Search over indexed chunks using the deployment strategy (`semantic` or `hybrid`).

**Request:**

```json
{
  "query": "What is the refund policy?",
  "top_k": 5,
  "document_id": null,
  "metadata_filter": { "source": "handbook" },
  "strategy": "hybrid",
  "rerank": true
}
```

| Field | Required | Notes |
| ----- | -------- | ----- |
| `query` | yes | 1–4096 characters |
| `top_k` | no | Default from `APE_RETRIEVAL__DEFAULT_TOP_K` |
| `document_id` | no | Restrict hits to one document |
| `metadata_filter` | no | Allowlisted keys only; others stripped |
| `strategy` | no | `semantic` or `hybrid`; default from config |
| `rerank` | no | Override `APE_RETRIEVAL__RERANK_ENABLED` |

**Score semantics:** semantic-only results use `1 - cosine_distance`. Hybrid
results expose the final RRF or reranker score.

**Response:**

```json
{
  "success": true,
  "data": {
    "query": "What is the refund policy?",
    "top_k": 5,
    "results": [
      {
        "chunk_id": "…",
        "document_id": "…",
        "chunk_index": 0,
        "content": "…",
        "score": 0.87,
        "filename": "handbook.txt",
        "page_number": 1,
        "char_start": 0,
        "char_end": 120,
        "metadata": {}
      }
    ]
  }
}
```

## Re-embed after the pgvector cutover

The native-vector migration returns documents with legacy packed embeddings to
`chunked`. Rebuild them through the unchanged endpoints:

1. `POST /api/v1/projects/{project_id}/documents/{document_id}/embed`
2. Poll until `embedded`, then call `POST .../index`
3. Poll until `ready` and validate semantic/hybrid search

Bulk re-embedding remains an operator/admin-script concern.

Operational sequence and validation queries:
[pgvector operations runbook](../learning/pgvector-operations-runbook.md).
