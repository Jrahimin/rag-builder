# Organizations — `/api/v1/organizations`

Organization management and API key lifecycle. Requires a logged-in **Super Admin** browser session.

**Auth header (admin):**

```http
Cookie: ape_admin_access=<HttpOnly session cookie>
```

## POST /api/v1/organizations

Create an organization.

**Request**

```json
{
  "name": "Acme Corp",
  "description": "Primary tenant"
}
```

**Response** `201`

```json
{
  "success": true,
  "data": {
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "name": "Acme Corp",
    "description": "Primary tenant",
    "is_active": true,
    "deleted_at": null,
    "deleted_by": null,
    "created_at": "2026-07-08T12:00:00Z",
    "updated_at": "2026-07-08T12:00:00Z"
  }
}
```

---

## GET /api/v1/organizations

List organizations (paginated).

**Query:** `limit`, `offset`, `include_deleted`, `is_active`

---

## GET /api/v1/organizations/{organization_id}

Get organization by id.

---

## PATCH /api/v1/organizations/{organization_id}

Update name/description.

---

## PUT /api/v1/organizations/{organization_id}/status

Set the operational state explicitly with `{ "is_active": true|false }`. Disabling blocks new
Project and API-key creation and API-key rotation without losing history.

---

## DELETE /api/v1/organizations/{organization_id}

Soft-delete organization.

## POST /api/v1/organizations/{organization_id}/restore

Restore an archived organization in the disabled state. An explicit status update is required to
re-enable it.

## GET /api/v1/organizations/{organization_id}/projects

List Projects associated with this client boundary, including inactive Projects for administration.

---

## POST /api/v1/organizations/{organization_id}/api-keys

Create a named API key. **Full secret returned once.**

**Request**

```json
{
  "name": "Production"
}
```

**Response** `201`

```json
{
  "success": true,
  "data": {
    "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "organization_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "name": "Production",
    "key_prefix": "ape_live_abc123",
    "secret": "ape_live_abc123…full_key_shown_once",
    "created_at": "2026-07-08T12:00:00Z",
    "updated_at": "2026-07-08T12:00:00Z",
    "last_used_at": null,
    "revoked_at": null
  }
}
```

---

## GET /api/v1/organizations/{organization_id}/api-keys

List API keys (metadata only; no secrets).

---

## POST /api/v1/organizations/{organization_id}/api-keys/{key_id}/rotate

Create a named replacement key first. The old key remains valid by default, allowing callers to
deploy the replacement before explicitly revoking the old credential. Emergency replace-and-revoke
requires `revoke_old=true` and an explicit confirmation in the request body. Key metadata exposes
`status`, `last_used_at`, `created_by`, and `rotated_from_key_id`; only the new secret is returned,
and only in this response.

**Response** `201` — same shape as create (includes new `secret`).

---

## DELETE /api/v1/organizations/{organization_id}/api-keys/{key_id}

Revoke key (idempotent).

---

## Business API authentication

Organization keys authenticate:

- `/api/v1/projects/**`
- `/api/v1/projects/{project_id}/documents/**`
- `/api/v1/projects/{project_id}/search`
- `/api/v1/projects/{project_id}/conversations/**`

```http
Authorization: Bearer ape_live_…
# or
X-API-Key: ape_live_…
```

**Errors:** `unauthorized` (401), `rate_limited` (429, includes `Retry-After` header)
