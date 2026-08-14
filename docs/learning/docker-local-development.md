# Docker Local Development: Build the Little City

> **Mental model:** RAG Builder is one deployable stack made of cooperating
> processes. The root Compose file uses immutable production images locally and
> on an ordinary single VPS.

```text
operator console -> backend API -> Redis queue -> Taskiq worker
                        │
                        ├── PostgreSQL + pgvector
                        ├── MinIO object storage
                        └── ClamAV upload scanning
```

## Start the full stack

From the repository root:

```bash
cp .env.example .env
# Replace every placeholder and set the correct HTTPS CORS origin.
docker compose up -d --build
```

`.env` is the ignored runtime and secret configuration file. `.env.example` is
the tracked template, so normal Compose commands need no flags.

| Surface | Loopback URL |
| --- | --- |
| Operator console | `http://127.0.0.1:3010/operator/` |
| API | `http://127.0.0.1:8010` |
| Liveness / readiness | `http://127.0.0.1:8010/health/live` / `health/ready` |
| PostgreSQL | `127.0.0.1:5433` |
| Redis | `127.0.0.1:6380` |
| MinIO API / console | `127.0.0.1:9010` / `127.0.0.1:9011` |

All bindings are loopback-only. ClamAV remains available only on the Docker
network.

The production frontend image is static-only: host Nginx owns API routing. For
the local static console to call the loopback API, set
`VITE_API_ORIGIN=http://127.0.0.1:8010` in `.env` and allow
`http://127.0.0.1:3010` in CORS before `docker compose up -d --build`. Host-side
Vite keeps its development-only `/api` proxy. This local HTTP-only mode also
requires `APE_AUTH__ADMIN_COOKIE_SECURE=false`; never copy that setting to the
VPS.

## Target individual services

```bash
# Infrastructure for host-side API development
docker compose up -d postgres redis minio minio-init

# Immutable API and worker
docker compose up -d --build backend worker

# Frontend alone; its API-unavailable state is intentional
docker compose up -d --build --no-deps frontend

# Observe an upload moving from API to worker
docker compose logs -f backend worker redis
```

Compose waits for PostgreSQL before the one-shot Alembic migration and for
MinIO before the one-shot bucket bootstrap. The API waits for both gates plus
healthy PostgreSQL, Redis, MinIO, and ClamAV. The worker waits for the database,
queue, storage, migration, and bucket, but not ClamAV: uploads are scanned by
the API before background processing.

## Fast reload on the host

Docker deliberately does not mount source or run reload servers. For fast
iteration, run infrastructure in Compose and the application processes on the
host:

```bash
docker compose up -d postgres redis minio minio-init

cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements/dev.txt
cp .env.example .env
# Configure host endpoints 5433, 6380, and 9010 in backend/.env.
alembic upgrade head
python -m app
```

Run the frontend separately with Vite:

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

The host development files may enable reload and localhost CORS. They are not
Docker deployment configuration and must not be copied to the VPS.

## Readiness and persistence

`/health/live` proves the API process is alive. `/health/ready` also checks
PostgreSQL, migration head, pgvector dimensions, Redis, S3-compatible storage,
and ClamAV. Cached external AI provider checks may be `degraded` without making
the core readiness endpoint fail. Taskiq workers publish expiring Redis heartbeats
shown by the operator API.

Named volumes preserve database, queue, object, and ClamAV signature data across
`docker compose down`. Do not use `docker compose down -v` unless destroying all
local data is intentional.

## Related

- [Knowledge Ingestion — End to End](./knowledge-ingestion-journey.md)
- [Configuration System](./configuration-system.md)
- [Database and Migrations](./database-and-migrations.md)
- [Deployment Architecture](../architecture/deployment-architecture.md)
