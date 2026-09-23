---
name: cur-rag-eval
description: Run RAG evaluation smoke and optional rag-journey fixtures, then report quality differences. Invoke explicitly with /cur-rag-eval.
disable-model-invocation: true
---

# RAG evaluation

Use when the user invokes `/cur-rag-eval`.

Follow `AGENTS.md`. If the user `cur-dev` run policy is available, read that too. Default is check-only: do not change product code.

From the backend venv:

```text
make eval-smoke
```

When the user names a fixture or compare:

```text
cd backend
python -m app.cli rag-journey --fixture <path> --repeat <n>
```

Add `--compare` only when they asked for a comparison. Write artifacts under `artifacts/rag-journey/` as the CLI already does.

Report commands, working directories, pass/fail, and what changed versus the previous run when a baseline exists. Implementation bugs return as findings, not silent fixes.
