# Single-VPS production deployment

The ordinary deployment topology is:

```text
Internet → Cloudflare (Full strict) → host Nginx :443
                                         ├─ 127.0.0.1:3010 → frontend
                                         └─ 127.0.0.1:8010 → FastAPI/Uvicorn ×2
                                                               ├─ Taskiq worker
                                                               ├─ PostgreSQL+pgvector
                                                               ├─ Redis
                                                               ├─ MinIO
                                                               └─ ClamAV
```

The root Compose file is used both locally and here. `infra/hosted` is a
separate specialized hosted-pilot contract.

## First deployment

1. Install Docker Engine with the Compose plugin and host Nginx. Check out the
   release at `/opt/rag-builder`; never expose the Docker daemon over TCP.
2. Copy `.env.docker.example` to `.env.docker`. Set the real HTTPS CORS origin
   and replace every placeholder with a unique secret. Rotate any credential
   that has been shared outside the VPS.
3. Keep `APE_EMBEDDING__DIMENSIONS=384` with the configured
   `text-embedding-3-small` provider. Migration creates `vector(384)` and API
   and worker startup verify both provider output and the migrated column.
4. Protect the runtime file and validate the resolved Compose model:

   ```bash
   chmod 600 .env.docker
   docker compose config --quiet
   ```

5. Install `nginx/rag-builder.conf`, replace the hostname/certificate paths,
   run `sudo nginx -t`, reload Nginx, and configure Cloudflare SSL/TLS as
   **Full (strict)**. Restrict origin HTTP(S) to Cloudflare networks or use an
   equivalent authenticated-origin control before trusting
   `CF-Connecting-IP`.
6. Start and verify:

   ```bash
   docker compose up -d --build
   docker compose ps
   curl --fail http://127.0.0.1:8010/health/ready
   curl --fail https://rag-builder.example.com/health/ready
   ```

`migrate` waits only for PostgreSQL. `minio-init` waits only for MinIO. The API
waits for both gates and healthy PostgreSQL, Redis, MinIO, and ClamAV. The
Taskiq worker waits for the gates and its database, queue, and storage; it does
not need ClamAV because the API scans each upload before enqueueing work.

## Network and operations

Only ports 80/443 and restricted administration access are public. Docker
publishes frontend `3010`, API `8010`, PostgreSQL `5433`, Redis `6380`, and
MinIO `9010`/`9011` on `127.0.0.1` only. Use SSH tunnels for infrastructure
debugging. ClamAV has no host port.

The deployment uses production image targets, two direct Uvicorn workers, a
separate Taskiq worker, named volumes, restart policies, service healthchecks,
and three 10 MiB Docker-local log files per service.

```bash
docker compose logs -f backend worker
docker compose ps
```

Before an upgrade, take matching PostgreSQL and MinIO backups. Deploy the new
release with the normal `up -d --build` command and repeat readiness and upload
smoke tests. Roll back by restoring matching database/object-storage backups;
do not assume an Alembic downgrade is safe.
