# Single-VPS production deployment

The ordinary deployment topology is:

```text
Internet → Cloudflare (Full strict) → host Nginx :443
                                         ├─ 127.0.0.1:3010 → frontend
                                         └─ 127.0.0.1:8010 → FastAPI/Uvicorn ×2
                                                               ├─ Taskiq worker
                                                               ├─ PostgreSQL+pgvector
                                                               ├─ Redis
                                                               ├─ S3-compatible storage (MinIO)
                                                               └─ ClamAV
```

The root Compose file is used both locally and here. `infra/hosted` is a
separate specialized hosted-pilot contract.

## First deployment

1. Install Docker Engine with the Compose plugin and host Nginx. Check out the
   release at `/opt/rag-builder`; never expose the Docker daemon over TCP.
2. Copy `.env.example` to `.env`. Set the real HTTPS CORS origin
   and replace every placeholder with a unique secret. Rotate any credential
   that has been shared outside the VPS.
3. Keep `APE_EMBEDDING__DIMENSIONS=384` with the configured
   `text-embedding-3-small` provider. Migration creates `vector(384)` and API
   and worker startup verify both provider output and the migrated column.
4. Protect the runtime file and validate the resolved Compose model:

   ```bash
   chmod 600 .env
   docker compose config --quiet
   ```

5. Install `nginx/rag-builder.conf`, replace the hostname/certificate paths,
   then generate the host-only Cloudflare real-IP include from the published
   ranges:

   ```bash
   { curl -fsSL https://www.cloudflare.com/ips-v4; curl -fsSL https://www.cloudflare.com/ips-v6; } \
     | sed -e 's#^#set_real_ip_from #' -e 's#$#;#' \
     | sudo tee /etc/nginx/snippets/cloudflare-realip.conf >/dev/null
   printf 'real_ip_header CF-Connecting-IP;\nreal_ip_recursive on;\n' \
     | sudo tee -a /etc/nginx/snippets/cloudflare-realip.conf >/dev/null
   ```

   Run `sudo nginx -t`, reload Nginx, and configure Cloudflare SSL/TLS as
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
waits for both gates and healthy PostgreSQL, Redis, S3-compatible storage, and ClamAV. The
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

## Backup and restore

Before upgrades, take matching PostgreSQL and object-storage backups and copy
both off the VPS. Store the database as a portable compressed dump and mirror
the artifact bucket to an encrypted remote S3-compatible backup bucket:

```bash
mkdir -p /var/backups/rag-builder
docker compose exec -T postgres sh -ec 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > /var/backups/rag-builder/postgres-$(date +%F-%H%M).dump
export BACKUP_S3_ENDPOINT=https://s3.backup.example.com
export BACKUP_S3_ACCESS_KEY=replace-from-secret-store
export BACKUP_S3_SECRET_KEY=replace-from-secret-store
docker compose run --rm --entrypoint /bin/sh \
  -e BACKUP_S3_ENDPOINT -e BACKUP_S3_ACCESS_KEY -e BACKUP_S3_SECRET_KEY minio-init -ec '
  mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
  mc alias set remote-backup "$BACKUP_S3_ENDPOINT" "$BACKUP_S3_ACCESS_KEY" "$BACKUP_S3_SECRET_KEY"
  mc mirror --overwrite "local/$MINIO_BUCKET" "remote-backup/rag-builder/$MINIO_BUCKET"'
sha256sum /var/backups/rag-builder/*.dump > /var/backups/rag-builder/SHA256SUMS
```

Read the `BACKUP_S3_*` values from the host secret store rather than placing
them in `.env`. The remote bucket must be off-VPS, versioned, and encrypted.
Retain the dump, matching object mirror, image version, and checksum manifest
together. Test restore quarterly.

To restore: stop `backend` and `worker`, restore PostgreSQL with `pg_restore`
into a clean database, mirror the matching object prefix back to MinIO, then run
`docker compose up -d --build`. Verify Alembic head/vector dimensions,
`/health/ready`, a worker heartbeat, and a sample document retrieval. Do not
assume an Alembic downgrade is safe.
