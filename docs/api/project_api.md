# Projects — `/api/v1/projects`

Project is the platform isolation boundary. All other modules scope data by `project_id`.

When `APE_AUTH__ENABLED=true`, all project routes require an Organization API key (`Authorization: Bearer ape_live_…` or `X-API-Key`). Projects are scoped to the authenticated organization.

## POST /api/v1/projects

Create a project.

**Request**

```json
{
  "name": "Acme Audit",
  "description": "Optional description"
}
```

**Response** `201`

```json
{
  "success": true,
  "data": {
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "name": "Acme Audit",
    "description": "Optional description",
    "is_active": true,
    "deleted_at": null,
    "deleted_by": null,
    "created_at": "2026-06-29T12:00:00Z",
    "updated_at": "2026-06-29T12:00:00Z"
  }
}
```

**Errors:** `project_name_conflict` (409)

---

## GET /api/v1/projects

List projects (paginated).

**Query:** `limit` (1–100, default 20), `offset`, `include_deleted` (default false), `is_active` (optional filter)

**Response** `200`

```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "name": "Acme Audit",
        "description": null,
        "is_active": true,
        "deleted_at": null,
        "deleted_by": null,
        "created_at": "2026-06-29T12:00:00Z",
        "updated_at": "2026-06-29T12:00:00Z"
      }
    ],
    "total": 1,
    "limit": 20,
    "offset": 0
  }
}
```

---

## GET /api/v1/projects/{project_id}

Get one project by id.

**Response** `200` — single `ProjectResponse` in `data`

**Errors:** `project_not_found` (404) — includes soft-deleted projects (treated as not found)

**Note:** `DELETE` still returns the deleted entity body; use list with `include_deleted=true` to inspect deleted rows.

---

## PATCH /api/v1/projects/{project_id}

Update name and/or description (partial).

**Request**

```json
{
  "name": "Renamed Project",
  "description": null
}
```

**Response** `200` — updated `ProjectResponse` in `data`

**Errors:** `project_not_found` (404), `project_deleted` (409), `project_name_conflict` (409)

---

## PATCH /api/v1/projects/{project_id}/status

Flip operational status (`is_active`). No request body — each call toggles the
current value (`true` → `false` → `true`, …).

**Response** `200` — updated `ProjectResponse` in `data`

**Errors:** `project_not_found` (404), `project_deleted` (409)

---

## DELETE /api/v1/projects/{project_id}

Soft-delete (sets `deleted_at`, `is_active=false`). Idempotent if already deleted.

**Response** `200` — `ProjectResponse` with `deleted_at` set

**Errors:** `project_not_found` (404)
