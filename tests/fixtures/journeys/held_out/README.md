# Held-out turn-resolution fixtures

Do not add held-out scenarios, queries, or expected bindings to this repository.

Development Journey packs (`tax_v1`, `business_conversation_v1`) are tuning data.
A later release check authors unseen cases outside implementation sessions, locks
the dataset hash, and scores them with the production Journey harness:

```text
python -m app.cli rag-journey --fixture <external-held-out>/journey.json
```

Scoring rules, denominators, and the freeze protocol live in
`docs/features/turn_resolution_held_out.md`.
