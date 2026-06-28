# Configuration System

This document explains how APE loads, structures, and consumes configuration —
the foundation for environment-driven, provider-agnostic operation.

---

## Why centralized configuration?

Hardcoded connection strings, model names, or chunk sizes make a platform
undeployable and untestable. APE resolves **everything** from the environment
at startup, with a single `Settings` object as the source of truth.

Future precedence (per architecture rules):

```text
defaults  →  environment  →  database  →  per-Project overrides
```

The foundation implements the first two layers.

---

## Technology: Pydantic Settings

`backend/app/core/config.py` defines nested config models and a root `Settings`
class extending `BaseSettings`.

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APE_",
        env_nested_delimiter="__",
        env_file=(".env",),
        extra="ignore",
    )
    app: AppConfig = Field(default_factory=AppConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    # ...
```

### Environment variable naming

Nested fields map to double-underscore env keys:

| Setting | Environment variable |
| ------- | -------------------- |
| `app.env` | `APE_APP__ENV` |
| `database.host` | `APE_DATABASE__HOST` |
| `logging.render_json` | `APE_LOGGING__RENDER_JSON` |
| `qdrant.api_key` | `APE_QDRANT__API_KEY` |

### Environments

`APE_APP__ENV` accepts: `development`, `testing`, `production`.

Tests set `APE_APP__ENV=testing` in `tests/conftest.py` before importing the app.

---

## Configuration sections

| Section | Model | Purpose |
| ------- | ----- | ------- |
| `app` | `AppConfig` | Name, version, env, API prefix |
| `server` | `ServerConfig` | Host, port, reload, workers |
| `logging` | `LoggingConfig` | Level, JSON vs console rendering |
| `cors` | `CORSConfig` | Allowed origins, methods, headers |
| `database` | `DatabaseConfig` | PostgreSQL DSN, pool settings |
| `redis` | `RedisConfig` | Redis DSN |
| `qdrant` | `QdrantConfig` | Qdrant host, ports, API key |
| `minio` | `MinioConfig` | S3-compatible storage endpoint |

### Database DSN generation

`DatabaseConfig` builds URLs via SQLAlchemy's `URL.create()` (handles special
characters in passwords):

```python
database.async_dsn  # postgresql+asyncpg://...
database.sync_dsn   # postgresql+psycopg://... (Alembic tooling)
```

---

## Access pattern: `get_settings()`

```python
@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- Cached for the process lifetime (parse env once).
- Injected via `Depends(get_settings)` as `SettingsDep`.
- Tests clear cache: `get_settings.cache_clear()`.

---

## Docker vs local development

`.env.example` documents two consumers:

1. **Application** — `APE_*` variables read by Pydantic Settings.
2. **Docker Compose** — `POSTGRES_*`, `MINIO_*`, port mappings for service containers.

In Docker, compose **overrides** hostnames to service names (`postgres`, `redis`,
`qdrant`, `minio`). Locally, defaults point to `localhost`.

---

## Design decisions

| Decision | Rationale |
| -------- | --------- |
| `APE_` prefix | Avoid collisions with system or library env vars |
| Nested models | Group related settings; validate as a unit |
| No secrets in code | `.env` is gitignored; `.env.example` has placeholders |
| `extra="ignore"` | Unknown env vars do not crash startup |
| Separate compose vars | Service provisioning vs application config |

---

## Common mistakes

| Mistake | Fix |
| ------- | --- |
| Reading `os.environ` directly | Use `get_settings()` |
| Hardcoding `localhost` in services | Read from `settings.database.host` |
| Forgetting to copy `.env.example` | Run `cp .env.example .env` |
| Single-underscore nesting (`APE_DATABASE_HOST`) | Use `APE_DATABASE__HOST` (double underscore) |

---

## Key files

| File | Role |
| ---- | ---- |
| `backend/app/core/config.py` | All config models + `get_settings()` |
| `.env.example` | Documented template |
| `docker-compose.yml` | Service host overrides for containers |
| `tests/unit/test_config.py` | DSN, CORS, env flag unit tests |
