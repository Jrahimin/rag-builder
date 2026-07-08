# Organization API Key Authentication — Implementation Plan

**Status:** Complete — Phase 1  
**Overview:** Machine-to-machine authentication for the public REST API using
**Organization-scoped API keys**. Not user/session auth. Preserves **Project** as
the data isolation boundary; Organization is the tenant/auth boundary.

Supports both deployment models:

- **Dedicated (single customer):** one Organization, many Projects, one or more API keys
- **Multi-tenant SaaS:** many Organizations, each with Projects and their own keys

---

## Domain model

```text
Deployment
  └── Organization              ← auth / tenant boundary (organization_id)
        ├── OrganizationApiKey  ← one or more active keys (named, rotatable)
        └── Project(s)          ← data isolation (project_id, FK → organization_id)
              └── Documents, chunks, search, chat…
```

| Entity | Role |
| ------ | ---- |
| **Organization** | Tenant/customer; must be `is_active` for API access |
| **OrganizationApiKey** | Named credential (`Production`, `CI/CD`, …); multiple active keys allowed |
| **Project** | Work corpus; scoped by `organization_id` + `project_id` |

**URLs unchanged:** `/api/v1/projects/{project_id}/...` — key answers *who*; path answers *which corpus*.

---

## Phase 1 scope

### In scope

1. Organization CRUD (admin bootstrap key)
2. **Multiple active API keys** per Organization with human-readable `name`
3. Key create, list, rotate (non-destructive until old key revoked), revoke
4. FastAPI **dependency-only** authentication (no auth in middleware)
5. Organization active check during auth (401 for bad key; deny inactive org)
6. Single-query project authorization (`project_id` + `organization_id`)
7. Constant-time hash verification (`hmac.compare_digest`)
8. **Short-lived verified-key cache** (Redis or process memory, 30–60s TTL) to avoid DB lookup on every request
9. **Organization-level rate limiting** via Redis (configurable, 429 + `Retry-After`)
10. `projects.organization_id` FK + migration backfill
11. Tests, ADR-012, feature doc, API reference

### Out of scope (explicit non-goals)

- User accounts, JWT, OAuth, RBAC, members
- Per-project API keys
- Key expiration / TTL
- IP allowlists or IP-based rate limiting
- Advanced audit logging beyond structured logs + `last_used_at`
- Worker/job HTTP authentication (jobs remain internal)

---

## Security design

### API key format

- Prefix: `ape_live_` + url-safe random (32 bytes)
- **Full secret shown once** on create/rotate response only
- Persist: `key_prefix` (display/lookup hint), `key_hash`, never plaintext
- Hash: `HMAC-SHA256(pepper, raw_key)` → hex or bytes; pepper from `APE_AUTH__KEY_PEPPER`
- Verify: recompute hash, compare with **`hmac.compare_digest`** (constant-time)

### Transport

Support both (document both; OpenAPI uses Bearer):

```http
Authorization: Bearer ape_live_...
X-API-Key: ape_live_...
```

### Credential tiers

| Tier | Routes | Credential |
| ---- | ------ | ---------- |
| **Deployment admin** | `/api/v1/organizations/**` | `APE_AUTH__ADMIN_API_KEY` (env) |
| **Organization** | `/api/v1/projects/**` and nested business routes | Organization API key |
| **Public** | `GET /health`, `GET /ready` | None |

### Authentication checks (dependency layer)

On every organization-key protected request, **in order**:

1. Extract raw key from `Authorization: Bearer` or `X-API-Key`
2. Missing/malformed → **401** `unauthorized`
3. **Verified-key cache lookup** (see below) → hit → skip DB; use cached `AuthenticatedOrganization`
4. Cache miss → DB lookup by `key_hash` + **`hmac.compare_digest`** → invalid/revoked → **401**
5. Load Organization → `deleted_at` set or `is_active=false` → **401** (treat as invalid credentials; do not distinguish inactive org from bad key in response body)
6. Cache successful verification (short TTL); bind `AuthenticatedOrganization` on `request.state` for logging
7. Apply rate limit (see below) → exceeded → **429** + `Retry-After`
8. Proceed to route handler / downstream Depends

> **Note:** Use 401 for inactive organizations (not 403) so clients cannot enumerate org state. Log `organization_inactive` at warn level internally.

### Verified-key cache (Phase 1)

Without caching, every authenticated request hits the database for hash lookup and org status. At scale that becomes a hot path.

**Not permanent storage** — a short-lived positive cache only:

```text
Request with API key
  → cache lookup (key_hash or cache key derived from raw key hash)
  → HIT  → AuthenticatedOrganization (skip DB)
  → MISS → DB verify + compare_digest → populate cache → continue
```

| Property | Value |
| -------- | ----- |
| TTL | **30–60 seconds** (configurable; default 60) |
| Backends | **Redis** (preferred, shared across API workers) or **process memory** (dev/single-worker fallback) |
| Cache value | `organization_id`, `organization_is_active`, `api_key_id` (minimal fields for auth + logging) |
| Negative caching | **No** — do not cache failed lookups (avoids stale deny after key creation) |

**Invalidate / expire on:**

- Key revoke (`DELETE .../api-keys/{id}`)
- Key rotate when `revoke_old=true` (invalidate old key entry)
- Organization soft-delete or `is_active=false` toggle (invalidate all keys for that org — or rely on TTL + org check on miss; prefer explicit invalidation on admin mutations)

Swappable contract (same pattern as rate limiter):

```text
platform/auth/contracts.py              → VerifiedKeyCache protocol
platform/infra/auth/redis_verified_key_cache.py
platform/infra/auth/memory_verified_key_cache.py   # dev / single process
dependencies/auth.py                    → cache → DB on miss
```

Cache key example: `ape:auth:verified:{key_hash}` — never store the raw secret in Redis.

### Multiple keys + zero-downtime rotation

- An Organization may have **multiple non-revoked keys** simultaneously
- **Create:** `POST .../api-keys` → new active key; existing keys unchanged
- **Rotate:** `POST .../api-keys/{key_id}/rotate` → creates **new** key; old key stays active until explicitly revoked (optional `revoke_old=true` query param defaults `false` for zero-downtime; when `true`, revokes the rotated key in same transaction)
- **Revoke:** `DELETE .../api-keys/{key_id}` → sets `revoked_at`; idempotent

Each key has required `name: str` (1–64 chars, unique per organization among non-revoked keys).

---

## Rate limiting (Phase 1)

### Design

- **After successful authentication** only (organization_id known)
- Key: `organization_id` (not client IP)
- Backend: Redis (existing deployment infrastructure)
- Swappable via abstract contract in `platform/` (implementation in `platform/infra/rate_limit/`)

```text
platform/rate_limit/contracts.py     → RateLimiter protocol
platform/infra/rate_limit/redis_rate_limiter.py
dependencies/auth.py                 → calls limiter after auth resolves org
```

### Algorithm (Phase 1)

Fixed window or sliding window counter in Redis — keep simple:

```text
KEY: ape:ratelimit:org:{organization_id}:{window_bucket}
INCR + EXPIRE
```

Configurable per deployment:

```env
APE_AUTH__RATE_LIMIT_ENABLED=true
APE_AUTH__RATE_LIMIT_REQUESTS=1000      # max requests per window
APE_AUTH__RATE_LIMIT_WINDOW_SECONDS=60
```

### HTTP 429 response

- Raise new `RateLimitError` (`status_code=429`, `code=rate_limited`)
- Set response header `Retry-After: <seconds>` (integer seconds until window reset)
- Exception handler in `core/exception_handlers.py` must pass through `Retry-After`

When `APE_AUTH__RATE_LIMIT_ENABLED=false` or Redis unavailable: **fail open** in dev (log warning); **fail closed** optional env flag for production (`APE_AUTH__RATE_LIMIT_FAIL_OPEN=false` default in production docs).

---

## Auth architecture: dependencies only

**Middleware must NOT authenticate.** `RequestContextMiddleware` may only enrich context **after** auth dependencies run (via callback or reading `request.state.authenticated_organization` set by Depends).

```text
Request
  → RequestContextMiddleware (request_id, trace_id only)
  → Router
  → Depends(require_organization_api_key)   ← cache → DB on miss → rate limit → request.state org
  → Depends(ensure_project_accessible)      ← single DB query
  → Service
```

Optional post-auth enrichment: extend `bind_request_context(organization_id=...)` in logging when `request.state.authenticated_organization` is set (read in middleware `finally` block or a thin `OrganizationContextMiddleware` that **only reads** `request.state` — no credential parsing).

---

## Project authorization (single query)

Replace `ensure_project_exists` with `ensure_project_accessible`:

```python
# ProjectRepository
async def get_by_id_for_organization(
    self,
    project_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> Project | None:
    """Single query: id + organization_id + not deleted."""
```

- Found → return project
- Not found → **404** `project_not_found` (same as today — do not reveal cross-org existence)
- Use in all `dependencies/knowledge.py`, `retrieval.py`, `conversations.py`, `projects.py`

**Project list/create:**

- `GET /projects` → repository filters `WHERE organization_id = :auth_org_id`
- `POST /projects` → service sets `organization_id` from auth context
- Unique name constraint → `(organization_id, name)` partial unique index where `deleted_at IS NULL`

---

## Code structure

### New module: `modules/organizations/`

```text
modules/organizations/
  services/
    organization_service.py
    api_key_service.py          # generate, hash, verify, create, rotate, revoke
  repositories/
    organization_repository.py  # AsyncRepository
    api_key_repository.py
  schemas/
    organization.py
    api_key.py
```

### ORM (`app/models/`)

```text
organization.py
organization_api_key.py
```

Update `project.py`: add `organization_id` FK.

Register in `composition/orm_registry.py`.

### Platform helpers

```text
platform/domain/api_key_crypto.py    # generate_key, hash_key, verify_key (compare_digest)
platform/domain/auth_context.py      # AuthenticatedOrganization (frozen dataclass)
platform/http/auth_headers.py        # extract_api_key(request) → str | None
platform/auth/contracts.py           # VerifiedKeyCache protocol
platform/infra/auth/redis_verified_key_cache.py
platform/infra/auth/memory_verified_key_cache.py
platform/rate_limit/contracts.py     # RateLimiter protocol
platform/infra/rate_limit/redis_rate_limiter.py
```

### Composition layer

```text
dependencies/auth.py                 # require_admin_api_key, require_organization_api_key
dependencies/organizations.py
dependencies/projects.py             # ensure_project_accessible (single query)
api/v1/routes/organizations_router.py
core/config.py                       # AuthConfig
core/exceptions.py                   # RateLimitError(retry_after_seconds: int)
core/exception_handlers.py           # Retry-After header on 429
```

### Router wiring (`api/v1/router.py`)

```python
# Admin routes — Depends(require_admin_api_key) on router
api_v1_router.include_router(organizations_router, prefix="/organizations", ...)

# Business routes — Depends(require_organization_api_key) on router
projects_router = APIRouter(dependencies=[Depends(require_organization_api_key)])
```

Apply org auth dependency to existing business routers (projects, documents, search, conversations).

---

## API surface

### Admin routes (`APE_AUTH__ADMIN_API_KEY`)

Prefix: `/api/v1/organizations`

| Method | Path | Description |
| ------ | ---- | ----------- |
| POST | `/organizations` | Create organization |
| GET | `/organizations` | List (paginated) |
| GET | `/organizations/{organization_id}` | Get |
| PATCH | `/organizations/{organization_id}` | Update name/description |
| PATCH | `/organizations/{organization_id}/status` | Toggle `is_active` (no body) |
| DELETE | `/organizations/{organization_id}` | Soft delete |
| POST | `/organizations/{organization_id}/api-keys` | Create key (`name` required); returns secret once |
| GET | `/organizations/{organization_id}/api-keys` | List keys (id, name, prefix, created_at, last_used_at, revoked_at) |
| POST | `/organizations/{organization_id}/api-keys/{key_id}/rotate` | Create new key; optional `?revoke_old=true` |
| DELETE | `/organizations/{organization_id}/api-keys/{key_id}` | Revoke key |

### Business routes (organization API key + rate limit)

Unchanged paths; add auth dependency:

- `/api/v1/projects/**`
- `/api/v1/projects/{project_id}/documents/**`
- `/api/v1/projects/{project_id}/search`
- `/api/v1/projects/{project_id}/conversations/**`

---

## Configuration

Single on/off for auth — **no `APE_AUTH__REQUIRE_AUTH`**. Avoid ambiguous combinations (`ENABLED=false` + `REQUIRE=true`). When `APE_AUTH__ENABLED=false`, all protected routes skip credential checks (local dev / tests only).

```env
# Auth
APE_AUTH__ENABLED=true                   # false = auth disabled (dev/tests)
APE_AUTH__ADMIN_API_KEY=...              # bootstrap admin (organizations CRUD)
APE_AUTH__KEY_PEPPER=...                 # min 32 bytes random; required when enabled

# Verified-key cache (short-lived; not permanent)
APE_AUTH__VERIFY_CACHE_ENABLED=true
APE_AUTH__VERIFY_CACHE_TTL_SECONDS=60    # 30–60 recommended
APE_AUTH__VERIFY_CACHE_BACKEND=redis     # redis | memory

# Rate limiting (organization-scoped, Redis)
APE_AUTH__RATE_LIMIT_ENABLED=true
APE_AUTH__RATE_LIMIT_REQUESTS=1000
APE_AUTH__RATE_LIMIT_WINDOW_SECONDS=60
APE_AUTH__RATE_LIMIT_FAIL_OPEN=false     # true in dev if Redis optional
```

Add to `backend/.env.example`.

---

## Migration plan

1. Create `organizations` table
2. Create `organization_api_keys` table (`name`, `key_prefix`, `key_hash`, `revoked_at`, `last_used_at`, FK → organization)
3. Add nullable `projects.organization_id`
4. Data migration: insert default Organization (`"Default"`), assign all existing projects
5. Set `projects.organization_id` NOT NULL + FK + index
6. Drop old `uq_projects_name`; add `uq_projects_org_name` on `(organization_id, name)` WHERE `deleted_at IS NULL`
7. Unique partial index on api keys: `(organization_id, name)` WHERE `revoked_at IS NULL`

Revision ID: keep ≤32 chars for `alembic_version.version_num`.

---

## Implementation todos

| ID | Task |
| -- | ---- |
| auth-config | `AuthConfig` in `core/config.py`; `.env.example` (single `ENABLED` flag) |
| crypto | `platform/domain/api_key_crypto.py` with `hmac.compare_digest` |
| verify-cache | `VerifiedKeyCache` protocol + Redis + memory implementations |
| rate-limit-contract | `RateLimiter` protocol + Redis implementation |
| orm-migration | Organization, OrganizationApiKey, Project.organization_id + backfill |
| org-module | repositories, services, schemas; cache invalidation on revoke/rotate/org deactivate |
| auth-deps | `dependencies/auth.py` — cache → DB on miss; admin + org auth; rate limit; `request.state` |
| org-router | `organizations_router.py` + mount |
| project-scoping | Project repo/service/router org filters; `get_by_id_for_organization` |
| wire-business | Auth dependency on business routers; update knowledge/retrieval/conversations deps |
| exceptions | `RateLimitError` + 429 handler with `Retry-After` |
| logging | Bind `organization_id` after auth (read from `request.state`) |
| unit-tests | crypto, verify cache, api_key_service, rate limiter, organization_service |
| integration-tests | org CRUD, auth 401/429, cache hit/miss, cross-org isolation, update existing suites |
| docs | ADR-012, feature doc, API reference, update domain-ownership + cursor rules |

---

## Implementation order

1. Config + crypto + `AuthenticatedOrganization` + `RateLimitError`
2. Verified-key cache contract + Redis + memory implementations
3. Rate limiter contract + Redis implementation
4. ORM + migration + backfill
5. Organization module (services/repos/schemas + cache invalidation hooks)
6. Auth dependencies (cache → DB on miss; admin + org + rate limit) — **no middleware auth**
7. Organizations router
8. Project org scoping + `ensure_project_accessible` single-query guard
9. Wire business routers
10. Tests (unit → integration; fix existing API tests with key fixtures)
11. Documentation

---

## Testing strategy

### Unit

- `test_api_key_crypto.py` — hash/verify, timing-safe compare
- `test_verified_key_cache.py` — hit/miss, TTL expiry, invalidation on revoke
- `test_api_key_service.py` — multiple active keys, rotate without revoke, revoke
- `test_redis_rate_limiter.py` — under/over limit, Retry-After calculation
- `test_organization_service.py` — CRUD lifecycle

### Integration

- Admin key required for `/organizations`
- Org key required for `/projects` when `APE_AUTH__ENABLED=true`
- 401: missing key, wrong key, revoked key, inactive organization
- 404: project not in org (single-query guard)
- 429: exceed org rate limit; assert `Retry-After` header
- Multiple keys: both work until one revoked
- Rotate: new key works; old key works until revoked
- Cache: second request with same key avoids DB (assert via mock/spy or metrics hook in test)

### Fixtures (`tests/conftest.py`)

- `admin_headers` — `Authorization: Bearer {ADMIN_API_KEY}`
- `organization_with_key` — creates org + named key via admin API
- `auth_headers` — org key for business API calls
- When `APE_AUTH__ENABLED=false` in test env, auth checks are bypassed (single flag; no separate require toggle)

---

## Reference files

**Patterns to mirror:**

- `backend/app/modules/projects/services/project_service.py`
- `backend/app/modules/projects/repositories/project_repository.py`
- `backend/app/api/v1/routes/projects_router.py`
- `backend/app/dependencies/projects.py`
- `backend/app/models/project.py`
- `backend/app/platform/domain/lifecycle_service.py`
- `backend/app/core/middleware.py` (context only — do not add auth here)
- `backend/app/platform/infra/connectivity/redis.py`
- `tests/integration/test_projects_api.py`

**Architecture docs:**

- `docs/architecture/adr/002-project-centric-ownership.md`
- `docs/architecture/module-architecture.md`
- `docs/architecture/domain-ownership.md`
- `docs/features/project_module.md`
- `backend/app/modules/_template/README.md`

**Update after ship:**

- `docs/features/organization_module.md`
- `docs/api/organization_api.md`
- `docs/architecture/adr/012-organization-api-key-auth.md`
- `.cursor/rules/project-context.mdc`, `architecture.mdc`
- `docs/plans/README.md` → mark Complete

---

## Acceptance criteria

- [ ] Multiple active named API keys per Organization
- [ ] Rotate creates new key; old key remains valid until explicit revoke (or `revoke_old=true`)
- [ ] Auth runs only in FastAPI Depends; middleware does not parse credentials
- [ ] Invalid/revoked key → 401; inactive/deleted org → 401
- [ ] Project access uses one query with `project_id` + `organization_id`
- [ ] Key verification uses `hmac.compare_digest`
- [ ] Verified-key cache (30–60s TTL); DB only on cache miss; invalidation on revoke/org deactivate
- [ ] Single `APE_AUTH__ENABLED` flag (no `REQUIRE_AUTH`)
- [ ] Rate limit keyed by `organization_id`; 429 with `Retry-After`
- [ ] `/health`, `/ready` remain public
- [ ] Existing projects backfilled to default Organization
- [ ] All tests pass; import boundaries hold

---

## ADR-012 decisions (summary)

| Decision | Rationale |
| -------- | --------- |
| Organization-scoped keys (not per-project) | One customer, many topic Projects |
| Multiple active keys | Zero-downtime rotation |
| Named keys | Operational clarity (`Production`, `CI/CD`) |
| Depends-only auth | Testable, explicit, matches architecture rules |
| 401 for inactive org | No tenant state enumeration |
| Single-query project guard | Fewer round trips; fail-closed |
| `hmac.compare_digest` | Timing-safe verification |
| Short-lived verified-key cache | Avoid per-request DB hot path at scale; Redis or memory, 30–60s TTL |
| Single `APE_AUTH__ENABLED` | One clear on/off; no ambiguous require flag |
| Redis org rate limit | Fair multi-tenant throttling; swappable contract |
| Admin bootstrap key | No chicken-and-egg for org provisioning |
