# Infrastructure

The root [`docker-compose.yml`](../docker-compose.yml) is the canonical stack
for local full-stack use and ordinary single-VPS production. It builds the
production frontend/backend targets and starts PostgreSQL+pgvector, Redis,
MinIO, ClamAV, migration/bootstrap jobs, the API, and the Taskiq worker.

Published ports are loopback-only: frontend `3010`, API `8010`, PostgreSQL
`5433`, Redis `6380`, and MinIO `9010`/`9011`. Follow the
[`vps/README.md`](vps/README.md) runbook for Cloudflare and host Nginx.

[`hosted/`](hosted/) is a separate, digest-pinned hosted-pilot contract with a
gateway and guarded recovery tooling. It is not an override or replacement for
the ordinary root Compose workflow.
