# API Versioning, Errors, Idempotency, and Async Work

- Public business routes are stable under `/api/v1`. Breaking request/response or
  semantic changes require `/api/v2`; additive optional fields and endpoints may ship in v1.
- The running OpenAPI contract is `/openapi.json`; application release `1.0.0` is the
  first sale-ready v1 contract. `/docs` is development-only.
- Success and failure bodies always use the documented envelope. Clients branch on
  `error.code`, never on message text. `trace_id` is the support correlation key.
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

