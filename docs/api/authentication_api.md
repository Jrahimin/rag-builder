# Super Admin Authentication API

These endpoints establish the browser session used by the RAG Builder operator
console. They are for the platform owner only; integrations must continue to
use Organization API keys on product endpoints.

All paths below are prefixed with `/api/v1/auth` and use the standard response
envelope.

## `POST /login`

Authenticates the bootstrapped Super Admin using email and password. On success
the response sets three cookies: short-lived HttpOnly access token,
HttpOnly refresh token, and a readable CSRF token for unsafe browser requests.

```json
{ "email": "owner@example.com", "password": "your-password" }
```

Invalid, unknown, and disabled accounts return the same `401` response. Login
attempts are rate-limited by email/IP fingerprint.

## `POST /refresh`

Rotates the opaque refresh token and issues a new access cookie. Send the
current `X-CSRF-Token` header matching the `ape_admin_csrf` cookie.

## `POST /logout`

Revokes the server-side browser session and clears all auth cookies. Requires
the matching `X-CSRF-Token` header.

## `GET /me`

Returns the authenticated Super Admin profile (`id`, `email`, `role`, and
`last_login_at`). A missing, expired, disabled, or revoked session returns
`401`.

## Browser security

Cookies are not returned in JSON and are never stored in localStorage. Configure
`APE_AUTH__ADMIN_COOKIE_SECURE=true` in production. The default `SameSite=lax`
works for same-site operator deployments; cross-site deployments require an
intentional `SameSite=none` plus Secure-cookie configuration.
