# Scripts

Operational and developer-convenience scripts for the AI Platform Engine.

Most day-to-day tasks are exposed through the root [`Makefile`](../Makefile)
(`make help`). This directory is reserved for standalone scripts that don't
fit a Make target, for example:

- database seeding / fixtures
- one-off maintenance and backfill jobs
- data export / import utilities
- local bootstrap helpers

> Keep scripts idempotent, Project-scoped where applicable, and free of
> hardcoded secrets (read configuration from the environment).
