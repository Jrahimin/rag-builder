# Knowledge API — Documents

**Prefix:** `/api/v1/projects/{project_id}/documents`

> The `/embed` and `/index` endpoints under this prefix are owned by the
> **retrieval** module — see [retrieval.md](./retrieval.md).

## POST ``

Upload a file (multipart field `file`). Enqueues `document.process` (`status=queued`).

Poll `GET /{document_id}` until `status=chunked` (or `failed`).

**413** — upload exceeds `APE_KNOWLEDGE__MAX_UPLOAD_BYTES` (default 50 MB).

## GET ``

List documents (paginated). Query: `limit`, `offset`, `include_deleted`.

## GET `/{document_id}`

Document metadata including `status`, `page_count`, `parser_name`, `error_message`, `parsed_text_storage_key`.

## GET `/{document_id}/chunks`

Paginated text chunks for a processed document.

Query: `limit` (1–100, default 20), `offset`.

**200 response**

```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": "770e8400-e29b-41d4-a716-446655440002",
        "document_id": "550e8400-e29b-41d4-a716-446655440000",
        "project_id": "660e8400-e29b-41d4-a716-446655440001",
        "chunk_index": 0,
        "content": "First segment of text...",
        "page_number": 1,
        "char_start": 0,
        "char_end": 512,
        "token_count": 85,
        "chunk_metadata": { "splitter": "recursive_character" },
        "created_at": "2026-06-30T12:00:00Z",
        "updated_at": "2026-06-30T12:00:00Z"
      }
    ],
    "total": 3,
    "limit": 20,
    "offset": 0
  }
}
```

**404** — document not found or wrong project scope.

## POST `/{document_id}/reprocess`

Re-enqueue full pipeline (parse + chunk). Bumps `document.version`; replaces existing chunks.

## DELETE `/{document_id}`

Soft-delete document, remove storage artifacts, delete chunk rows, and purge
retrieval artifacts (`chunk_embeddings` + best-effort vector points via
`RetrievalCleanupService`).
