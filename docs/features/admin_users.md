# Platform operator accounts

## Purpose

Human operators for one dedicated APE deployment. The CLI bootstraps the first
**Super Admin**. The operator console creates additional **Admin** accounts.
Both roles can sign in and use the full console today. Module-level permissions
are deferred.

## Architecture

```text
CLI `python -m app.cli.admin create`
        │  SUPER_ADMIN
        ▼
admin_users + admin_sessions
        ▲
Console `/operator/admins` → POST /api/v1/admin-users
        │  ADMIN
        ▼
Cookie session  `/api/v1/auth`
```

`require_super_admin` accepts either role. No extra permission checks.

## Rules

* Console create always stores `role=ADMIN`. Role is not a request field.
* Super Admin cannot be disabled or removed from the console.
* An operator cannot disable or remove their own account.
* Disable and remove revoke active sessions immediately.
* Remove is a soft delete. Restore comes back **disabled**.
* Email is unique among non-deleted rows.

## API

See [admin_users_api.md](../api/admin_users_api.md). Auth remains
[authentication_api.md](../api/authentication_api.md).
