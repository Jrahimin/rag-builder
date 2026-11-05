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

New Projects are ownership-locked at creation. Organization callers may omit `organization_id`
because it is taken from their API key. Super Admin callers must supply it explicitly. Responses
also include `organization_id`, `ownership_locked`, `active_ai_config_revision_id`, and
`source_metadata_generation`.

Project AI policy is not administered through Organization-authenticated Project routes. See the
Super-Admin endpoints in [operator_api.md](operator_api.md).

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

## Reliability configuration and source review additions

Project AI `execution.execution_policy` accepts `legacy`, `adaptive_v1`, `bounded_v2`,
and `bounded_extended_v2`. Send an explicit `execution.profile_id`, including `inherit`.
Execution/recovery overrides persist with inherited and preset profiles; an explicit null
clears an independent override. The operator Use Global controls send null for both
execution policy and bounded recovery; omission during a profile switch preserves them.
Effective responses and conversation snapshots expose the
resolved execution policy and immutable configuration identity. Existing Message request
shape is unchanged.

Optional `behavior.calendar_policy` uses an IANA timezone, period kind, year boundary,
label year offset/span and `default_for_current`; invalid zones/dates are rejected. These
fields establish requested period semantics, never governing legal authority.

`GET /api/v1/projects/{project_id}/sources/review-queue?limit=100&offset=0` returns
`ApiResponse[list[GovernanceReviewProposal]]` from current processing-version chunks.
These are untrusted proposals; pagination counts chunks, and a chunk may contribute more
than one proposal. Existing source-revision and candidate scope-review writes remain the
acceptance boundary. New whole-document replacement declarations carry
`review_provenance.whole_document_replacement: "declared"`. Unrelated metadata correction
preserves reviewed edge payloads. Changing supporting chunk identity, quote or offsets
invalidates the saved review and evidence hash even when the new span is a valid member
of the same source. Title-only changes with identical proof retain their review.
Sparse corrections retain the current edition interval; a changed edition does not inherit
the preceding edition end date.
