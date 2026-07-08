# ADR-012: Organization API Key Authentication

**Status:** Accepted  
**Date:** 2026-07-08

## Context

APE exposes a public REST API for enterprise integrations. Phase 1 needs machine-to-machine authentication without user accounts, JWT, or OAuth. Customers may deploy dedicated (one tenant) or multi-tenant SaaS (many tenants). **Project** remains the data isolation boundary; **Organization** is the tenant/auth boundary.

## Decision

1. **Organization-scoped API keys** — not per-project keys. One customer may own many Projects under one Organization.
2. **Multiple active named keys** per Organization for zero-downtime rotation (`Production`, `CI/CD`, …).
3. **Depends-only authentication** — no credential parsing in middleware. `require_organization_api_key` / `require_admin_api_key` in `dependencies/auth.py`.
4. **Admin bootstrap key** (`APE_AUTH__ADMIN_API_KEY`) for `/api/v1/organizations/**` provisioning.
5. **Key format:** `ape_live_` + url-safe random; store `key_prefix` + HMAC-SHA256 hash with deployment pepper; verify with `hmac.compare_digest`.
6. **Transport:** `Authorization: Bearer` or `X-API-Key`.
7. **401 for invalid/revoked keys and inactive/deleted organizations** — no tenant state enumeration in responses.
8. **Single-query project guard** — `get_by_id_for_organization(project_id, organization_id)`.
9. **Short-lived verified-key cache** (Redis or memory, 30–60s TTL) to avoid per-request DB hash lookup.
10. **Organization-scoped rate limiting** via Redis (configurable; 429 + `Retry-After`).
11. **Single `APE_AUTH__ENABLED` flag** — when `false`, auth checks are bypassed (dev/tests only).
12. **Auth domain events for cache invalidation** — services publish `OrganizationAuthInvalidated` / `ApiKeyAuthInvalidated` after commit; `VerifiedKeyCacheEventHandler` in composition invalidates cache (no direct cache calls from module services).

## Consequences

- All business routes (`/projects/**`, nested modules) require an Organization API key when auth is enabled.
- `projects.organization_id` FK scopes projects to organizations; unique name constraint is `(organization_id, name)`.
- Existing projects are backfilled to a default Organization on migration.
- Cache invalidation on key revoke, org deactivate/delete, and rotate-with-revoke — via domain events, after DB commit.

## Alternatives considered

| Alternative | Why rejected |
| ----------- | ------------ |
| Per-project API keys | Operational overhead for multi-project customers |
| JWT / user sessions | Out of Phase 1 scope; adds identity layer |
| Middleware auth | Harder to test; violates explicit Depends pattern |
| 403 for inactive org | Enables tenant state enumeration |

## References

- `docs/plans/organization_auth_module_plan.md`
- `docs/features/organization_module.md`
