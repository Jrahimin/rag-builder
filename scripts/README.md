# Scripts

Operational and developer-convenience scripts for the AI Platform Engine.

This directory is for standalone scripts that do not belong in the main app
package, for example:

- database seeding / fixtures
- one-off maintenance and backfill jobs
- data export / import utilities
- local bootstrap helpers

> Keep scripts idempotent, Project-scoped where applicable, and free of
> hardcoded secrets (read configuration from the environment).
