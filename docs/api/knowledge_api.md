# Knowledge API — Documents

**Prefix:** `/api/v1/projects/{project_id}/documents`

> The `/embed` and `/index` endpoints under this prefix are owned by the
> **retrieval** module — see [retrieval_api.md](./retrieval_api.md).

## POST ``

Upload a file (multipart field `file`). Supported inputs are PDF, DOCX, UTF-8
TXT/Markdown, PNG, JPEG, TIFF, and WebP. Extension/MIME/signature, corruption,
password protection, and malware checks run before storage or job creation.
Enqueues `document.process` (`status=queued`).

Optional form field `ocr_lang` — per-document OCR language for scanned PDFs and image uploads. For
PDFs, omission allows Unicode script detection before falling back to `APE_OCR__LANG` (`en`).
Images and scans without a usable text layer go directly to the deployment default. Normalized
aliases include `eng`→`en` and `bangla`/`bengali`/`ben`→`bn`.

> **Bangla OCR:** enable `APE_OCR__BANGLA_BACKEND=google_vision` and configure its API key.
> Unicode Bangla PDFs can auto-route; scans, images, and custom-font/Bijoy PDFs require explicit
> `ocr_lang=bn` or deployment default `bn`. See [multilingual support](../features/multilingual_support.md#bangla-bengali-ocr-routing-and-limitations).

The response includes `job_id`. Inspect it through the
[Jobs API](./jobs_api.md), or poll the Document until `status=chunked` (or
`failed`).

**413** — upload exceeds `APE_KNOWLEDGE__MAX_UPLOAD_BYTES` (default 50 MB).

Stable upload failures include `document_type_unsupported`,
`document_mime_mismatch`, `document_signature_mismatch`, `document_corrupt`,
`document_password_protected`, `document_malware_detected`, and
`malware_scanner_unavailable`.

**Sample request** (Hindi/Devanagari scan — supported Paddle language):

```http
POST /api/v1/projects/{project_id}/documents
Content-Type: multipart/form-data

file=@scan.png
ocr_lang=hi
```

**201 response** (excerpt)

```json
{
  "success": true,
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "filename": "scan.png",
    "status": "queued",
    "ocr_lang": "hi",
    "language": null,
    "version": 1,
    "job_id": "880e8400-e29b-41d4-a716-446655440003"
  }
}
```

> Do **not** use `ocr_lang=bn` until a Bengali-capable OCR backend is added. For Bangla PDFs with a valid Unicode text layer, omit `ocr_lang` — native parsing works.

## GET ``

List documents (paginated). Query: `limit`, `offset`, `include_deleted`.

## GET `/{document_id}`

Document metadata including `status`, `page_count`, `parser_name`, `error_message`, `parsed_text_storage_key`, `language`, `ocr_lang`.

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
        "page_start": 1,
        "page_end": 1,
        "char_start": 0,
        "char_end": 512,
        "token_count": 85,
        "chunk_metadata": {
          "strategy_used": "markdown",
          "structure_score": 0.72,
          "section_title": "Introduction",
          "parsed_document_version": "1.0.0",
          "chunker_version": "2.0.0",
          "token_count_method": "approx_regex_v1"
        },
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

Returns `202` and re-enqueues the durable full pipeline. It bumps `document.version`, writes new
versioned chunks, creates a complete isolated vector+keyword build, validates it,
and atomically activates it while preserving the previous build.
The response includes the new durable `job_id`.

Optional query `ocr_lang` — set or override per-document OCR language for the new run. Pass empty string to clear and fall back to `APE_OCR__LANG`. When omitted, the existing `documents.ocr_lang` is kept.

> `ocr_lang=bn` selects the configured Bangla backend. It fails clearly when Google Vision is
> selected without a valid credential.

## DELETE `/{document_id}`

Stages a durable reversible delete and returns `202` with `job_id`. The job
builds and atomically activates a corpus excluding the document, then sets
`deleted_at`. Relational and storage artifacts remain available for rollback.

## DELETE `/{document_id}/purge`

Stages a durable irreversible purge and returns `202` with `job_id`. After an
excluding build is active, the job removes all document chunks, vectors, keyword
rows, raw/parsed storage objects, and the document row. Retained builds that
reference the document are superseded and cannot be reactivated.
