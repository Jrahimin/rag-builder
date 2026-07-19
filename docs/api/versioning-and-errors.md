# API Versioning, Errors, Idempotency, and Async Work

- Public business routes are versioned under `/api/v1`. The current `0.9.x`
  technical preview may still make breaking changes before a stable `1.0` contract.
- The running OpenAPI contract is `/openapi.json`; `/docs` is development-only.
- Success and failure bodies always use the documented envelope. Clients branch on
  `error.code`, never on message text. `request_id` is the public support correlation key.
- Lists use `{items,total,limit,offset}` and deterministic ordering as documented per
  endpoint. Clients must not infer completion from page length.
- Resource creation returns `201`. Accepted background work returns `202` and a
  `job_id`; document creation remains `201` and includes its initial job identity.
- Job submission is replay-safe through the persisted Project-scoped idempotency key.
  Upload creation is content/version scoped. Webhook receiver replay safety uses event ID.
- Claim/evidence fields in chat v1 are additive to the citation snapshot and are stable
  in streaming and non-streaming final results.

The platform intentionally does not publish an SDK in Phase 6. Generate internal client
types from OpenAPI or call HTTP directly until real integration feedback justifies an SDK.
