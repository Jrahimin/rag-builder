---
name: cur-investigate
description: Read-only root-cause investigation that writes one operations or plans note. Invoke explicitly with /cur-investigate.
disable-model-invocation: true
---

# Investigate

Use when the user invokes `/cur-investigate`.

Default is read-only against application code. You may add one note under `docs/operations/` or `docs/plans/` when they asked for a write-up.

Ground in logs, CI (`gh run view --log-failed`), the dirty tree, and existing notes in those folders. Reproduce with project commands when that is safe and local.

Do not commit, push, or repair unless they separately invoked `/cur-dev` or `/cur-ci`.

Report: symptom, evidence, likely cause, what was ruled out, and the next bounded task.
