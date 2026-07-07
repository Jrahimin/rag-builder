# Retrieval API

Hybrid and semantic search plus indexing endpoints. Requires documents at `status=ready`.

**Prefix:** `/api/v1/projects/{project_id}`

> `/documents/{document_id}/embed` and `/documents/{document_id}/index` are
> mounted on the documents router for URL consistency but are owned by the
> **retrieval** module.

Search results are filtered to the deployment's active `embedding_set_version`
(`APE_RETRIEVAL__EMBEDDING_SET_VERSION`) so vectors and keyword rows from prior
embedding runs are excluded.

Indexing (`POST .../index`) refreshes **both** Qdrant vector points and PostgreSQL
keyword index rows.

## POST `/documents/{document_id}/embed`

Enqueue embedding for a `chunked` document.

**Response `data.status`:** `embedding` (async) → `embedded` after worker

## POST `/documents/{document_id}/index`

Enqueue vector + keyword indexing for an `embedded` document.

**Response `data.status`:** `indexing` (async) → `ready` after worker

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

**Score semantics:** `results[].score` is the final ranking score (RRF fused or
reranker relevance), not raw cosine similarity.

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

## Reindex runbook (v2 upgrade)

After deploying Retrieval v2, reindex existing `ready` documents so keyword rows exist:

1. For each document: `POST /api/v1/projects/{project_id}/documents/{document_id}/index`
2. Poll `GET /documents/{id}` until `status=ready`
3. Verify hybrid search with `{"strategy":"hybrid","query":"..."}`

Bulk reindex API is out of scope for v2; use per-document index or an admin script.
