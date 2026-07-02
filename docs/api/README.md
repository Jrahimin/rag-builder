# API Reference

Concise endpoint reference for Postman collection preparation. OpenAPI (`/docs`) is the live contract.

**Base URL (local):** `http://localhost:8000`

## Response envelope

Success (`/api/v1/*`):

```json
{
  "success": true,
  "message": null,
  "data": {},
  "meta": { "request_id": null, "trace_id": null }
}
```

Error:

```json
{
  "success": false,
  "error": {
    "code": "project_not_found",
    "message": "Project not found.",
    "trace_id": "…",
    "details": null
  }
}
```

## Modules

| Module | File | Prefix |
| ------ | ---- | ------ |
| System | [system.md](system.md) | `/health`, `/ready` |
| Projects | [projects.md](projects.md) | `/api/v1/projects` |
| Knowledge | [knowledge.md](knowledge.md) | `/api/v1/projects/{project_id}/documents` |
| Retrieval | [retrieval.md](retrieval.md) | `/api/v1/projects/{project_id}` (search, embed, index on documents prefix) |
