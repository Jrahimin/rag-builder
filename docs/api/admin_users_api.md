# Admin users — `/api/v1/admin-users`

Manage human operator accounts for the operator console. Requires a logged-in
**Super Admin** or **Admin** browser session. Console-created users always get
`role=ADMIN`. The CLI bootstrap Super Admin is not created here.

**Auth:** `Cookie: ape_admin_access=<HttpOnly session cookie>` plus `X-CSRF-Token`
on unsafe methods.

## POST /api/v1/admin-users

Create an Admin. Password minimum 8 characters.

**Request**

```json
{
  "email": "ops@example.com",
  "password": "a-long-enough-password"
}
```

**Response** `201`

```json
{
  "success": true,
  "data": {
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "email": "ops@example.com",
    "role": "ADMIN",
    "is_active": true,
    "last_login_at": null,
    "deleted_at": null,
    "deleted_by": null,
    "created_at": "2026-08-24T12:00:00Z",
    "updated_at": "2026-08-24T12:00:00Z"
  }
}
```

## GET /api/v1/admin-users

List operators (paginated).

**Query:** `limit`, `offset`, `include_deleted`, `is_active`

## GET /api/v1/admin-users/{admin_user_id}

Get one operator. `include_deleted=true` to read a removed account.

## PUT /api/v1/admin-users/{admin_user_id}/status

Set `{ "is_active": true|false }`. Disabling signs the operator out. Super Admin
and the caller's own account are rejected.

## DELETE /api/v1/admin-users/{admin_user_id}

Soft-delete an Admin. Super Admin and the caller's own account are rejected.

## POST /api/v1/admin-users/{admin_user_id}/restore

Restore a removed Admin as **disabled**. Enable separately to allow sign-in.
