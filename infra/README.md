# Infrastructure

Deployment assets for local development and operator-managed dedicated hosting.

The local development stack is defined by the root
[`docker-compose.yml`](../docker-compose.yml) and the backend image at
[`backend/Dockerfile`](../backend/Dockerfile):

| Service | Image | Purpose | Local port(s) |
| --- | --- | --- | --- |
| frontend | `ape-frontend:dev` | React operator console | 3000 |
| backend | `ape-backend:dev` | FastAPI application and webhook dispatcher | 8000 |
| worker | `ape-backend:dev` | Durable Taskiq job execution | — |
| postgres | `pgvector/pgvector:pg16` | Relational, vector, keyword, job, and webhook state | 5432 |
| redis | `redis:7` | Background-job executor transport | 6379 |
| minio | `minio/minio` | S3-compatible object storage | 9000 / 9001 |
| clamav | `clamav/clamav` | Optional upload malware scanning | 3310 |

The supported Phase 6 hosted pilot profile and guarded operations tooling live in
[`hosted/`](hosted/). This is not customer-operated self-hosted packaging; that remains
Future F1. Kubernetes remains intentionally out of scope.
