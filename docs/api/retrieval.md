# Retrieval API

Semantic search and indexing endpoints. Requires documents at `status=ready`.

**Prefix:** `/api/v1/projects/{project_id}`

> `/documents/{document_id}/embed` and `/documents/{document_id}/index` are
> mounted on the documents router for URL consistency but are owned by the
> **retrieval** module.

Search results are filtered to the deployment's active `embedding_set_version`
(`APE_RETRIEVAL__EMBEDDING_SET_VERSION`) so vectors from prior embedding runs
are excluded.

## POST `/documents/{document_id}/embed`

Enqueue embedding for a `chunked` document.

**Response `data.status`:** `embedding` (async) → `embedded` after worker

## POST `/documents/{document_id}/index`

Enqueue vector indexing for an `embedded` document.

**Response `data.status`:** `indexing` (async) → `ready` after worker

## POST `/search`

Semantic search over indexed chunks.

**Request:**

```json
{
  "query": "What is the refund policy?",
  "top_k": 5,
  "document_id": null,
  "metadata_filter": { "source": "handbook" }
}
```

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
