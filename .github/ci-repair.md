# CI repair

Extends the global `/cur-ci` policy for this repo. Absent in other repos, the global skill still applies.

Protected branches: `main`

Validate the command named in the failing step. The `quality` job ends at `make quality`. When the log names an earlier Make target, rerun that target first (`make format-check`, `make lint`, `make typecheck`, `make test-unit`, `make test-integration`, `make migration-check`, `make migration-drift-check`, `make eval-smoke`, `make frontend-quality`).

Run `docker compose build` only when the failing job is `docker-builds`.

Escalate: schema or data migrations, auth changes, and assertion changes. Do not treat a format-only failure as a product change.
