# Dedicated Hosted Pilot Runbook

This is the operator contract for one isolated customer deployment. Commands run from
`infra/hosted/`. Keep release, runtime-secret, and TLS files out of version control.

## Onboard

1. Copy `release.env.example` to `release.env` and replace every zero digest from the
   approved release manifest. Copy `secrets/runtime.env.example` to `secrets/runtime.env`.
2. Record provider/file/ownership choices from `docs/operations/dedicated-onboarding.md`.
   Place `tls.crt` and `tls.key` in `secrets/` and provision DNS.
3. Run `python hostedctl.py validate`.
4. Run `docker compose --env-file release.env -f compose.yaml pull`, then
   `docker compose --env-file release.env -f compose.yaml up -d`.
5. Verify `/health/live`, `/health/ready`, console, active worker, configuration, upload, retrieval,
   grounded answer/refusal, signed webhook receipt, and a backup.

## Backup and restore verification

`python hostedctl.py backup` briefly quiesces writes, takes a PostgreSQL custom-format
dump and MinIO mirror, writes a version/image manifest, and resumes service. Copy the
whole directory to the approved encrypted backup location. Restore verifies the
deployment/database/bucket identity plus the PostgreSQL and per-object SHA-256 inventory
before it stops serving traffic.

Exercise restore before onboarding and after material schema/storage changes on a
disposable clone. Verify migration head, object and corpus counts, active index pointer,
search, grounded chat, and a new signed webhook delivery.

Restore overwrites the target database and object bucket and requires an exact token:

```bash
python hostedctl.py restore backups/<stamp> --confirm RESTORE:<deployment-id>
```

The backup manifest must match the dedicated deployment ID.

## Upgrade and rollback

1. Preserve the old `release.env` as `release.previous.env`.
2. Put approved new digests in `release.env`; run `python hostedctl.py validate`.
3. Run `python hostedctl.py upgrade`. It creates a consistent pre-upgrade backup before
   pulling, migrating, starting, and checking readiness.
4. Run the onboarding smoke path and inspect jobs/webhooks.

If migration, startup, readiness, or smoke verification fails, never improvise an
Alembic downgrade. Roll back data and images to the pre-upgrade snapshot:

```bash
python hostedctl.py rollback backups/<stamp> \
  --previous-release-env release.previous.env \
  --confirm ROLLBACK:<deployment-id>
```

## Reindex

Queue `/api/v1/projects/{project_id}/index-builds/reindex`, inspect the durable job/build,
validate, and activate explicitly. Retain the prior pointer until verification. Use the
index rollback API; never reconstruct the former active corpus in place.

## Key rotation

- Organization key: create replacement, deploy to the host app, verify, revoke old.
- Admin key/key pepper: use maintenance. Pepper rotation invalidates stored hashes;
  provision replacement organization credentials in the approved change.
- Webhook signing key: disable endpoints, rotate the deployment key, recreate endpoints,
  place new secrets in receivers, verify, and enable. All derived secrets change.
- Provider/DB/Redis/MinIO/TLS: update the secret source, restart affected services, and
  verify readiness plus the relevant smoke path. Prefer provider-native dual credentials.

## Incident and support diagnostics

1. Preserve request/event/job/delivery IDs and the time window. Disable a noisy receiver;
   never delete delivery history.
2. Check readiness, dependencies, workers, failures, jobs, and webhook attempts.
3. Run `python hostedctl.py diagnostics diagnostics/<incident-id>`. The bundle contains
   image/version facts, process state, bounded logs, and migration heads—not runtime secrets.
4. If integrity is uncertain, stop writes, take a backup, and run storage reconciliation.
5. Record cause, affected window, recovery evidence, and follow-up. Do not promise an SLO
   or notification window without an approved commercial contract.
