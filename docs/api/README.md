# API Reference

Concise endpoint reference for Postman collection preparation. OpenAPI (`/docs`) is the live contract.

> **Integrating APE into your application?** Start with the
> [Platform Integration Guide](../platform-integration-guide.md) — step-by-step flow,
> auth, polling, and copy-paste examples. Use this folder as the endpoint cheat sheet.

**Base URL (local):** `http://localhost:8000` (Docker) or `http://localhost:8088` (venv)

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
| System | [system_api.md](system_api.md) | `/health`, `/ready`, `/metrics` |
| Operator | [operator_api.md](operator_api.md) | `/api/v1/operator` |
| Organizations | [organization_api.md](organization_api.md) | `/api/v1/organizations` |
| Projects | [project_api.md](project_api.md) | `/api/v1/projects` |
| Knowledge | [knowledge_api.md](knowledge_api.md) | `/api/v1/projects/{project_id}/documents` |
| Jobs | [jobs_api.md](jobs_api.md) | `/api/v1/projects/{project_id}/jobs` |
| Index lifecycle | [index_lifecycle_api.md](index_lifecycle_api.md) | `/api/v1/projects/{project_id}/index-builds` |
| Retrieval | [retrieval_api.md](retrieval_api.md) | `/api/v1/projects/{project_id}` (search, embed, index on documents prefix) |
| Conversations | [conversation_api.md](conversation_api.md) | `/api/v1/projects/{project_id}/conversations` |
| Evaluation | [evaluation_api.md](evaluation_api.md) | `/api/v1/projects/{project_id}/evaluations` |
