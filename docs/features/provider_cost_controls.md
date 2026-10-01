# Provider expense controls

Exact embedding reuse removes repeated paid work while preserving full immutable corpus builds, retrieval thresholds, grounding, citations and existing activation checks. Cohere models remain unchanged. Reranking is metered and budgeted but not cached.

## Scope and data flow

Project HTTP requests (including SSE), durable jobs and explicit RAG journey runs attach a provider work context in the composition layer. Shared Cohere HTTP dispatch reserves spending in an independent PostgreSQL transaction before each HTTP attempt. Completion records `meta.billed_units.input_tokens` or `search_units`. HTTP retries produce separate attempts. An absent billing field, timeout or cancellation retains the conservative reservation, rather than reporting zero usage. Billing rows contain project/reference/workload/environment/model/purpose/price version and numbers, with no source text or credentials.

The platform embedding decorator checks a project-scoped persistent cache before calling Cohere. The key includes the exact input SHA-256, document/query purpose, provider, model, dimensions, adapter version, endpoint namespace, embedding set version and cache schema. Duplicate inputs retain their original order. Each batch of at most 96 missing inputs commits independently; completed work survives a failed build transaction. A project advisory lock serializes cache fills across workers. This trades some concurrent miss latency for avoiding duplicate paid work. Full index validation and atomic activation still run.

For DOCUMENT inputs at the canonical Cohere endpoint, sealed active/validated/retained corpus vectors with the exact hash and identity can seed the cache. Old adapters lack endpoint provenance, so noncanonical endpoints are excluded from this reuse. QUERY vectors stay separate. Cache rows store vectors and hashes, not input text; their TTL is 30 days by default. A document purge clears all project cache rows because passage entries do not track document ownership. Hard project deletion cascades cache deletion; billing history deliberately survives so deleting a temporary QA project cannot reset a monthly budget. Soft deletion retains data under the existing project lifecycle.

With ingestion debounce enabled, auto-build `document.embed` children with the same index configuration join one queued `corpus.reembed` job. The first arrival establishes a fixed outbox deadline. Later arrivals cannot postpone it indefinitely. Arrivals after that job becomes running create a queued successor. Corpus execution is serialized per project. Parsing/chunking still runs per document, and activation still checks the corpus manifest. Documents remain chunked while waiting. Manual corpus operations keep their existing behavior.

## Configuration and rollout

All keys use `APE_PROVIDER_COSTS__` in backend environment configuration. Defaults:

| Suffix | Default | Meaning |
| --- | --- | --- |
| `ENABLED` | false | Record every scoped Cohere HTTP attempt; reject unscoped paid calls |
| `CACHE_ENABLED` | false | Reuse exact Cohere inputs across builds and chat turns |
| `ENFORCE_BUDGETS` | false | Admit paid calls only within recorded monthly and operation budgets; requires accounting |
| `MONTHLY_BUDGET_USD` | 30 | Deployment-wide, UTC-calendar-month cap for recorded Cohere work |
| `OPERATION_BUDGET_USD` | 2 | Shared cap for one HTTP request or durable job reference |
| `EVALUATION_BUDGET_USD` | 1 | Shared cap for one explicit paid evaluation run |
| `PAID_EVALUATION_ENABLED` | false | Allow scoped evaluation work to use Cohere |
| `CACHE_TTL_DAYS` | 30 | Expired vectors are misses; project cleanup occurs during cache access |
| `EMBEDDING_USD_PER_MILLION` | 0.12 | Configured embed-v4.0 rate |
| `RERANK_USD_PER_UNIT` | 0.0025 | Configured rerank-v4.0-pro search-unit rate |
| `PRICE_VERSION` | cohere-v4-2026-09 | Identifies the configured price assumptions on each attempt |
| `BUILD_COALESCE_SECONDS` | 0 | 0 disables debounce; suggested initial value 10, maximum 300 |

1. Apply Alembic revision `20261001_0038` using the normal deployment migration process. Restart API and worker with `ENABLED=true` for observation. Cohere startup probes are reported **skipped, capability unverified**, avoiding unscoped paid work. Verify the provider through a deliberately scoped project operation.
2. Enable `CACHE_ENABLED=true`. Verify unchanged rebuilds and repeated passages reuse vectors; adding one document should embed only new exact inputs. Retain the existing corpus validation and current/historical answer checks.
3. With `APE_JOBS__DISPATCHER_ENABLED=true`, set `BUILD_COALESCE_SECONDS=10`. Do not enable positive debounce without a running durable dispatcher; delayed intents otherwise remain pending. Inline RAG journey fixtures force this value to zero.
4. After reviewing recorded spend and setting appropriate budgets, enable `ENFORCE_BUDGETS=true`. Limits are finite and positive. Admission is atomic across workers, using billed charges plus outstanding/unknown reservations. Supported enforced pricing pairs are `/v2/embed` + `embed-v4.0` and `/v2/rerank` + `rerank-v4.0-pro`; other models fail before dispatch until pricing support is added.

Embedding reservation estimates use UTF-8 bytes plus a 32-byte framing allowance per input as a conservative token proxy. Rerank estimates overcount virtual documents using byte length plus query length. Actual returned billing units replace estimates. These are application limits, not a Cohere account limit: calls from other deployments, tools or keys are absent, and calls made before activation are absent. Provider prices or billing behavior may change; verify rates against the account before enforcing a financial target. A separate Cohere account spending limit remains useful. Monthly sums include this deployment's projects and evaluation activity; each independently deployed database needs its own allocation.

## Operator use

From `backend`, with the virtualenv active:

```powershell
python -m app.cli provider-costs --project <project-uuid> --month 2026-10 --preview-build
```

This read-only command groups attempts, returned tokens/search units and accounted micro-USD by UTC day, workload, endpoint, purpose, model, environment and price version. Add `--reference <request/job/run-reference>` to isolate one operation. Unknown costs remain distinguishable. Preview reads current eligible chunks, existing cache and compatible sealed vectors without filling the cache or contacting Cohere. It estimates only currently missing inputs; when reuse is disabled it includes every chunk. The corpus can change before dispatch. One million micro-USD equals one USD. Historical invoice reconciliation requires Cohere dashboard data, not retrospective token guesses.

Ordinary pytest runs force hash embeddings, noop reranking and echo LLM defaults; external async HTTP transport is blocked even if a test explicitly constructs a paid adapter. Mock/ASGI transports remain supported. Explicit live QA stays separate:

```powershell
python -m app.cli rag-journey --fixture <fixture-path> --paid-eval --budget-usd 1
```

`--paid-eval` enables accounting and enforced finite evaluation limits for that process only. Baseline, repeats, comparisons, raw replay and inline child jobs share one run reference and budget. Current configured cache behavior is retained. The CLI requires the paid flag and budget on every invocation, even when deployment evaluation opt-in is enabled. The command refuses a Cohere run without opt-in and enforced budgets, before creating its temporary project. Other paid diagnostics must attach a provider work scope explicitly when accounting is enabled. Paid evaluations through the API also require deployment opt-in and enforced finite budgets, and consume the evaluation limit under their durable run ID.

Budget exhaustion produces nonretryable `provider_budget_exhausted` on durable jobs. Increase the approved limit or wait for a new month, then use the existing manual job retry. Completed cached batches remain reusable; a fresh job reference still consumes the deployment monthly limit. The active index remains intact. Chat and rerank failures follow the existing provider-error handling and degradation rules; inspect the ledger rather than assuming every accepted chat used paid reranking.

## Verification and limits

Unit tests cover exact identity separation, concurrent reuse, partial-batch failure, actual billing fields, timeout/cancellation, opt-in, finite limits and the external HTTP guard. PostgreSQL integration tests cover independent commits, expiry, advisory locks, project purge, atomic monthly/operation admission and fixed debounce deadlines with a running-job successor. Ordinary quality checks make no live Cohere calls.

The implementation does not migrate models, cache rerank results, modify evidence thresholds, create scheduled background jobs, change Cohere keys/account limits, apply the migration to production, or activate feature flags in the live environment. Cache hits still incur local validation/index work. The ledger is a forward-looking operational record; it cannot reconstruct September calls or guarantee an exact invoice saving.
