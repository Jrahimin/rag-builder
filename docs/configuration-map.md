# Configuration Map

Complete field-level map of every setting in `backend/app/core/config.py`.
Use this when you need to know **what a key means, what it changes, which env
var overrides it, and whether a Project can override it**.

Related (do not duplicate):

- Precedence and snapshots — [configuration architecture](./architecture/configuration-architecture.md)
- Beginner mental model — [configuration system](./learning/configuration-system.md)
- Project sparse policy — [project AI policy](./features/project_ai_policy_and_provenance.md)

Code defaults below are the Pydantic defaults. Root `.env.example` (Docker /
hosted) and `backend/.env.example` (local venv) override several RAG knobs;
those differences are called out.

---

## How to read a key

```text
APE_<SECTION>__<FIELD>     →    settings.<section>.<field>
APE_RETRIEVAL__STRATEGY    →    settings.retrieval.strategy
```

Prefix `APE_`, nested delimiter `__`, case-insensitive. Values load from the
process environment, then `.env` (`backend/.env` when the API runs from a
venv; repo-root `.env` for Docker Compose). Unknown `APE_*` keys are ignored.

| Scope | Meaning |
| ----- | ------- |
| **D** | Deployment only. Change via env. Not in Project AI revisions. |
| **P** | Project may set a sparse override. Omitted → inherit deployment. |
| **Req** | External request may set a *fixed allowlist* only (`top_k`, enabled `strategy`, allowlisted metadata filters, `as_of`). |
| **Legacy** | Still parsed; prefer the replacement. Do not set both unless you know the winner. |

Changing **index identity** (embedding backend/model/dimensions or
`embedding_set_version`) requires a new immutable index build and activation.
Changing **chunking/parsing/OCR** requires document reprocess, then rebuild.

---

## Layers (what actually wins)

```text
code default
    → env / .env                          (deployment Settings)
        → active Project AI revision      (sparse; secrets never stored)
            → request allowlist           (top_k / strategy / filters / as_of)
                → safety caps             (ai_policy, provider capabilities)
```

Jobs and conversations snapshot the **effective** secret-free config at enqueue /
conversation create. Later env or Project edits do not rewrite in-flight work.
Workers merge live credentials onto that snapshot.

Embedding model/dimensions and chunking are **not** Project policy: they are
coupled to the vector schema and index-artifact identity.

---

## Symptom → which knob

| You observe | Look at first | Not first |
| ----------- | ------------- | --------- |
| Upload rejected (413) | `knowledge.max_upload_bytes` | parsers |
| Scanned PDF / Bangla custom font empty | `ocr.enabled`, `ocr.backend`, `ocr.bangla_backend`, `ocr.lang` | LLM |
| Chunks too big / split mid-heading | `chunking.strategy`, token sizes, `structure_score_threshold` | temperature |
| Search returns nothing after provider change | `embedding.*` + `retrieval.embedding_set_version` + activate build | chat thresholds |
| Right chunk exists but ranked low | candidate `top_k`, `hnsw_ef_search`, RRF weights, `reranker_backend` | prompt |
| Cross-language miss | `query_translation.enabled`, `rerank_mode=always`, cross-language semantic bar | chunk overlap |
| Answer refuses with insufficient evidence | evidence gate + semantic/reranker/lexical floors | `max_tokens` |
| Answer cites weakly / hallucinates | `evidence_gate_mode=enforce`, `grounding_mode`, claim coverage | larger `top_k` |
| Slow search | `hnsw_ef_search`, candidate pools, rerank window, passage scoring | pool_size |
| Worker jobs stuck | `jobs.lease_seconds`, heartbeats, `runtime.worker_stale_seconds` | retrieval |

---

## 1. App, server, logging, CORS

Identity and process flags. Self-explanatory except `env`, which **gates
production safety** (`validate_runtime_config`, auth required, no hash/echo).

| Path | Env | Default | Scope | Meaning and options |
| ---- | --- | ------- | ----- | ------------------- |
| `app.name` | `APE_APP__NAME` | `AI Platform Engine` | D | Display name in logs / operator. Cosmetic. |
| `app.env` | `APE_APP__ENV` | `development` | D | `development` \| `testing` \| `production`. `testing` skips `.env` load. `production` requires a certified `runtime.profile`, real providers, auth, MinIO, ClamAV, Taskiq, hybrid+rerank. |
| `app.debug` | `APE_APP__DEBUG` | `true` | D | Framework debug flag. Keep `false` in production. |
| `app.version` | `APE_APP__VERSION` | `0.9.0` | D | Reported application version. |
| `app.api_v1_prefix` | `APE_APP__API_V1_PREFIX` | `/api/v1` | D | Mount path. Changing it breaks every client and the console. |
| `server.host` | `APE_SERVER__HOST` | `0.0.0.0` | D | Bind address for `python -m app`. |
| `server.port` | `APE_SERVER__PORT` | `8000` | D | Bind port (venv example uses `8088`). Compose maps the container separately. |
| `server.reload` | `APE_SERVER__RELOAD` | `false` | D | Uvicorn `--reload`. Local only. |
| `server.workers` | `APE_SERVER__WORKERS` | `1` | D | Uvicorn worker processes. API replicas, not the durable job worker. |
| `logging.level` | `APE_LOGGING__LEVEL` | `INFO` | D | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` \| `CRITICAL`. |
| `logging.render_json` | `APE_LOGGING__RENDER_JSON` | `false` | D | `true` = JSON for aggregators (Docker example). `false` = console renderer. |
| `cors.allow_origins` | `APE_CORS__ALLOW_ORIGINS` | `*` | D | CSV or JSON list. Production **forbids** `*`. Example: `https://console.example.com`. |
| `cors.allow_credentials` | `APE_CORS__ALLOW_CREDENTIALS` | `true` | D | Cookie/auth CORS. Must be `true` for Super Admin cookies. |
| `cors.allow_methods` | `APE_CORS__ALLOW_METHODS` | `*` | D | CSV / JSON. |
| `cors.allow_headers` | `APE_CORS__ALLOW_HEADERS` | `*` | D | CSV / JSON. |

---

## 2. Runtime profile and preflight

Certified stack selection. Wrong profile + provider mix fails startup in
production.

| Path | Env | Default | Scope | Meaning and options |
| ---- | --- | ------- | ----- | ------------------- |
| `runtime.profile` | `APE_RUNTIME__PROFILE` | `development` | D | See table below. Production cannot keep `development`. |
| `runtime.startup_timeout_seconds` | `APE_RUNTIME__STARTUP_TIMEOUT_SECONDS` | `30` | D | Whole preflight budget (Postgres, Redis, storage, ClamAV). Raise if slow networks abort boot. |
| `runtime.dependency_timeout_seconds` | `APE_RUNTIME__DEPENDENCY_TIMEOUT_SECONDS` | `3` | D | Per core-dependency probe. Used by `/ready`. |
| `runtime.provider_timeout_seconds` | `APE_RUNTIME__PROVIDER_TIMEOUT_SECONDS` | `15` | D | Background LLM/embed/rerank/OCR capability probe. Failure degrades capability; does not take the API down. |
| `runtime.worker_heartbeat_seconds` | `APE_RUNTIME__WORKER_HEARTBEAT_SECONDS` | `10` | D | How often a job worker writes a Redis heartbeat. Must be **<** stale. |
| `runtime.worker_stale_seconds` | `APE_RUNTIME__WORKER_STALE_SECONDS` | `35` | D | Age after which operator UI marks a worker stale. Must exceed heartbeat. |

**`runtime.profile` options**

| Value | LLM | Embeddings | When to use |
| ----- | --- | ---------- | ----------- |
| `development` | anything, including `echo` | anything, including `hash` | Local/tests. Forbidden in production. |
| `hosted_managed` | `openai` | `cohere` (`embed-v4.0` @ 1024) | Preferred dedicated hosted stack. Shared `APE_COHERE__API_KEY`. |
| `hosted_openai` | `openai` | `openai` | Deprecated compatibility. Must not require Cohere. |
| `private_ollama` | `ollama` | `ollama` | Private Ollama-compatible endpoint. |

Production also forces: `jobs.backend=taskiq`, dispatcher on, webhooks on,
`storage.backend=minio`, `malware_scan.backend=clamav`, `retrieval.strategy=hybrid`,
`rerank_enabled=true`, auth on, non-default secrets.

---

## 3. Database, Redis, storage, MinIO

Infrastructure. Compose maps service DNS and copies Compose secrets into the
`APE_*` keys (see [Compose aliases](#compose-only-keys-not-settings)).

| Path | Env | Default | Scope | Meaning |
| ---- | --- | ------- | ----- | ------- |
| `database.host` | `APE_DATABASE__HOST` | `localhost` | D | Postgres host. Compose sets `postgres`. |
| `database.port` | `APE_DATABASE__PORT` | `5432` | D | Postgres port inside the network. |
| `database.user` | `APE_DATABASE__USER` | `ape` | D | DB user. Required non-empty. |
| `database.password` | `APE_DATABASE__PASSWORD` | `ape` | D | Production rejects this default. |
| `database.name` | `APE_DATABASE__NAME` | `ape` | D | Database name. Integration tests must use the disposable name. |
| `database.echo` | `APE_DATABASE__ECHO` | `false` | D | Log SQL. Dev only. |
| `database.pool_size` | `APE_DATABASE__POOL_SIZE` | `5` | D | SQLAlchemy pool. Raise with API concurrency; watch Postgres `max_connections` (API + worker + dispatcher). |
| `database.max_overflow` | `APE_DATABASE__MAX_OVERFLOW` | `10` | D | Extra connections above pool_size. |
| `database.pool_timeout` | `APE_DATABASE__POOL_TIMEOUT` | `30` | D | Seconds to wait for a free connection. |
| `database.pool_recycle` | `APE_DATABASE__POOL_RECYCLE` | `1800` | D | Recycle connections (seconds). Avoids NAT/idle kills. |
| `test_database.name` | `APE_TEST_DATABASE__NAME` | `ape_test` | D | Pytest must migrate **this** DB only. |
| `test_database.allow_migrations` | `APE_TEST_DATABASE__ALLOW_MIGRATIONS` | `false` | D | Must be `true` for integration tests to run Alembic. |
| `redis.host` | `APE_REDIS__HOST` | `localhost` | D | Cache, rate-limit, worker heartbeat, Taskiq. Compose: `redis`. |
| `redis.port` | `APE_REDIS__PORT` | `6379` | D | Redis port. |
| `redis.db` | `APE_REDIS__DB` | `0` | D | Logical DB index. |
| `redis.password` | `APE_REDIS__PASSWORD` | `null` | D | Required non-default in production. |
| `storage.backend` | `APE_STORAGE__BACKEND` | `local` | D | `local` = filesystem; `minio` = S3-compatible. Production requires `minio`. |
| `storage.local_root` | `APE_STORAGE__LOCAL_ROOT` | `./storage` | D | Directory for `local`. Required when backend is local. |
| `minio.endpoint` | `APE_MINIO__ENDPOINT` | `localhost:9000` | D | Host:port, no scheme. Compose: `minio:9000`. |
| `minio.access_key` | `APE_MINIO__ACCESS_KEY` | `minioadmin` | D | Production rejects default. |
| `minio.secret_key` | `APE_MINIO__SECRET_KEY` | `minioadmin` | D | Production rejects default. |
| `minio.secure` | `APE_MINIO__SECURE` | `false` | D | `true` = HTTPS to the object store. |
| `minio.region` | `APE_MINIO__REGION` | `us-east-1` | D | S3 region string. |
| `minio.bucket` | `APE_MINIO__BUCKET` | `ape-artifacts` | D | Raw + parsed artifacts. Must exist (Compose `minio-init` creates it). |

---

## 4. Jobs

Durable outbox + leased worker. `inline` runs the handler in-process (tests).

| Path | Env | Default | Scope | Meaning |
| ---- | --- | ------- | ----- | ------- |
| `jobs.backend` | `APE_JOBS__BACKEND` | `taskiq` | D | `taskiq` (Redis broker) \| `inline`. Production requires `taskiq`. |
| `jobs.dispatcher_enabled` | `APE_JOBS__DISPATCHER_ENABLED` | `true` | D | Polls outbox and delivers to the broker. Production requires `true`. |
| `jobs.dispatcher_poll_seconds` | `APE_JOBS__DISPATCHER_POLL_SECONDS` | `1` | D | Outbox poll interval. Lower = faster start, more DB load. |
| `jobs.dispatcher_batch_size` | `APE_JOBS__DISPATCHER_BATCH_SIZE` | `50` | D | Rows claimed per poll. |
| `jobs.lease_seconds` | `APE_JOBS__LEASE_SECONDS` | `300` | D | How long a worker owns a job before recovery. Must **exceed** heartbeat. Raise for slow OCR/embed. |
| `jobs.heartbeat_seconds` | `APE_JOBS__HEARTBEAT_SECONDS` | `30` | D | Lease heartbeat while running. Must be **<** lease. |
| `jobs.max_attempts` | `APE_JOBS__MAX_ATTEMPTS` | `3` | D | Durable retry budget after failure. |
| `jobs.retry_base_delay_seconds` | `APE_JOBS__RETRY_BASE_DELAY_SECONDS` | `2` | D | Exponential backoff base after handler failure. |
| `jobs.retry_max_delay_seconds` | `APE_JOBS__RETRY_MAX_DELAY_SECONDS` | `300` | D | Cap on retry delay. Must be ≥ base. |
| `jobs.dispatch_retry_base_seconds` | `APE_JOBS__DISPATCH_RETRY_BASE_SECONDS` | `1` | D | Backoff when broker delivery itself fails. |
| `jobs.dispatch_retry_max_seconds` | `APE_JOBS__DISPATCH_RETRY_MAX_SECONDS` | `60` | D | Cap on dispatch backoff. Must be ≥ base. |

---

## 5. Webhooks

Signed outbound document-outcome callbacks. Signing key is a **secret**, not a
Project setting.

| Path | Env | Default | Scope | Meaning |
| ---- | --- | ------- | ----- | ------- |
| `webhooks.enabled` | `APE_WEBHOOKS__ENABLED` | `true` | D | Master switch. Production requires `true`. |
| `webhooks.dispatcher_enabled` | `APE_WEBHOOKS__DISPATCHER_ENABLED` | `true` | D | Requires `enabled`. Production requires `true`. |
| `webhooks.signing_key` | `APE_WEBHOOKS__SIGNING_KEY` | `development-only-webhook-signing-key` | D | HMAC key, ≥ 32 bytes. Production rejects the default. |
| `webhooks.dispatcher_poll_seconds` | `APE_WEBHOOKS__DISPATCHER_POLL_SECONDS` | `1` | D | Attempt-table poll interval. |
| `webhooks.dispatcher_batch_size` | `APE_WEBHOOKS__DISPATCHER_BATCH_SIZE` | `50` | D | Deliveries claimed per poll. |
| `webhooks.delivery_timeout_seconds` | `APE_WEBHOOKS__DELIVERY_TIMEOUT_SECONDS` | `10` | D | HTTP timeout to the customer URL. |
| `webhooks.delivery_lease_seconds` | `APE_WEBHOOKS__DELIVERY_LEASE_SECONDS` | `60` | D | Must **exceed** delivery timeout so a slow POST is not double-sent. |
| `webhooks.max_attempts` | `APE_WEBHOOKS__MAX_ATTEMPTS` | `6` | D | Delivery retries. |
| `webhooks.retry_base_seconds` | `APE_WEBHOOKS__RETRY_BASE_SECONDS` | `5` | D | Backoff base. |
| `webhooks.retry_max_seconds` | `APE_WEBHOOKS__RETRY_MAX_SECONDS` | `3600` | D | Backoff cap (1h). Must be ≥ base. |
| `webhooks.response_excerpt_chars` | `APE_WEBHOOKS__RESPONSE_EXCERPT_CHARS` | `1000` | D | Stored body excerpt for diagnostics. Not a secret store. |

---

## 6. Knowledge, malware, parsing

Ingestion limits and PDF text quality. These freeze into the **job snapshot**.
Reprocess documents after changing them.

| Path | Env | Default | Scope | Meaning and options |
| ---- | --- | ------- | ----- | ------------------- |
| `knowledge.max_upload_bytes` | `APE_KNOWLEDGE__MAX_UPLOAD_BYTES` | `52428800` (50 MB) | D | HTTP 413 above this. Raise for large PDFs; uploads spool to disk, not RAM. |
| `malware_scan.backend` | `APE_MALWARE_SCAN__BACKEND` | `disabled` | D | `disabled` (dev/test) \| `clamav`. Production requires `clamav`. |
| `malware_scan.host` | `APE_MALWARE_SCAN__HOST` | `localhost` | D | ClamAV daemon. Compose: `clamav`. |
| `malware_scan.port` | `APE_MALWARE_SCAN__PORT` | `3310` | D | clamd port. |
| `malware_scan.timeout_seconds` | `APE_MALWARE_SCAN__TIMEOUT_SECONDS` | `15` | D | Scan timeout. Raise for huge files. |
| `parsing.pdf_text_parsers` | `APE_PARSING__PDF_TEXT_PARSERS` | `pymupdf,pdfium` | D | Ordered native PDF extractors. First acceptable page wins. CSV. |
| `parsing.min_page_quality_score` | `APE_PARSING__MIN_PAGE_QUALITY_SCORE` | `0.55` | D | Unicode/text quality 0–1. Below → try next parser or OCR. Lower if digital PDFs look “empty”; raise if garbage text is accepted. |
| `parsing.min_document_success_ratio` | `APE_PARSING__MIN_DOCUMENT_SUCCESS_RATIO` | `0.2` | D | Fraction of pages that must pass. Below → document `failed`. |
| `parsing.min_text_chars` | `APE_PARSING__MIN_TEXT_CHARS` | `20` | D | Page with fewer characters is treated as empty (scan candidate). |

**Example:** a 200-page scan with 10 text pages. Quality 0.55 + success ratio 0.2
→ native parse fails the document unless OCR is enabled and recovers pages.

---

## 7. Chunking (RAG — ingestion)

How parsed text becomes retrieval units. **Deployment-only.** Wrong sizes
cannot be fixed by chat thresholds. Token counts are **approximate**
(`unicode_property_v1`), not the embedding tokenizer.

| Path | Env | Default | Scope | Meaning and options |
| ---- | --- | ------- | ----- | ------------------- |
| `chunking.strategy` | `APE_CHUNKING__STRATEGY` | `auto` | D | See strategy table. Prefer `auto`. |
| `chunking.target_tokens` | `APE_CHUNKING__TARGET_TOKENS` | `250` | D | Aim per chunk. Smaller → more precise hits, more chunks/cost. Larger → more context per hit, noisier. |
| `chunking.max_tokens` | `APE_CHUNKING__MAX_TOKENS` | `400` | D | Hard cap; oversized blocks split (tables split by row groups). |
| `chunking.min_tokens` | `APE_CHUNKING__MIN_TOKENS` | `50` | D | Merge tiny fragments. v3 invariant: no sub-min chunk unless the whole doc is smaller. |
| `chunking.overlap_tokens` | `APE_CHUNKING__OVERLAP_TOKENS` | `50` | D | **Reserved.** Recursive fallback does **not** currently overlap. Changing it will not change output. |
| `chunking.structure_score_threshold` | `APE_CHUNKING__STRUCTURE_SCORE_THRESHOLD` | `0.55` | D | `auto` uses structure/heading chunking when analysis score ≥ this. Lower → more heading splits on weak PDFs; higher → more semantic splits. |
| `chunking.long_block_token_threshold` | `APE_CHUNKING__LONG_BLOCK_TOKEN_THRESHOLD` | `600` | D | Structure analyzer treats a block as “long” above this; encourages split of huge sections. |
| `chunking.similarity_drop_threshold` | `APE_CHUNKING__SIMILARITY_DROP_THRESHOLD` | `0.35` | D | Semantic strategy: sentence-similarity drop that starts a new chunk. Lower → more splits; higher → longer topical chunks. |
| `chunking.semantic_batch_size` | `APE_CHUNKING__SEMANTIC_BATCH_SIZE` | `32` | D | Embedding batch for semantic boundaries. Latency/cost only. |
| `chunking.chunker_version` | `APE_CHUNKING__CHUNKER_VERSION` | `3.0.0` | D | Provenance stamp in snapshots. Bump when algorithm changes; reprocess. |
| `chunking.token_count_method` | `APE_CHUNKING__TOKEN_COUNT_METHOD` | `unicode_property_v1` | D | Unicode letter/number/mark counting (ADR-010). Do not revert to regex `\w`. |
| `chunking.ocr_confidence_threshold` | `APE_CHUNKING__OCR_CONFIDENCE_THRESHOLD` | `0.5` | D | `auto`: low OCR quality → **semantic** strategy (OCR noise has weak headings). |

**`chunking.strategy` options**

| Option | What it does | Use when |
| ------ | ------------ | -------- |
| `auto` | Rule order: tables → low OCR → mixed language → markdown → DOCX headings → structured PDF/HTML → semantic. | Default. Leave it. |
| `markdown` | Split on markdown headings. | Forced `.md` corpora. |
| `heading` | Split on document headings (DOCX). | Forced heading layout. |
| `structure` | Keep tables/sections together; split oversized tables with repeated headers. | Forms, tax tables. |
| `semantic` | Split on embedding similarity drops. Needs a real embedder, not `hash`. | Prose with weak headings. |
| `recursive_fallback` | Length-based split of raw text. | Last resort / oversized leftover. |
| `recursive_character` | Alias of `recursive_fallback` (same splitter). | Do not prefer; redundant enum. |

**Example:** Bangla scanned gazette → OCR then `auto` picks semantic because OCR
confidence is below 0.5. Forcing `heading` on that corpus produces garbage
boundaries.

---

## 8. OCR

Disabled by default. Native PDF text still runs first except the Bangla
OCR-first route (ADR-017). Enable for scans, images, Bijoy/custom-font PDFs.

| Path | Env | Default | Scope | Meaning and options |
| ---- | --- | ------- | ----- | ------------------- |
| `ocr.enabled` | `APE_OCR__ENABLED` | `false` | D | Master switch. Hosted example sets `true`. |
| `ocr.backend` | `APE_OCR__BACKEND` | `noop` | D | `noop` \| `paddle` \| `google_vision`. General page fallback. Production forbids enabled+noop. |
| `ocr.bangla_backend` | `APE_OCR__BANGLA_BACKEND` | `noop` | D | Bengali route. `google_vision` is the certified Bangla path. |
| `ocr.lang` | `APE_OCR__LANG` | `en` | D | Deployment fallback language. Per-upload `ocr_lang` wins. Scans cannot auto-detect Bangla Unicode — send `ocr_lang=bn` or set `bn`. |
| `ocr.use_gpu` | `APE_OCR__USE_GPU` | `false` | D | Paddle GPU. Irrelevant for Google Vision. |
| `ocr.google_api_key` | `APE_OCR__GOOGLE_API_KEY` | `null` | D | Required if either backend is `google_vision`. |
| `ocr.google_endpoint` | `APE_OCR__GOOGLE_ENDPOINT` | Vision `images:annotate` URL | D | Override only for a proxy. |
| `ocr.google_timeout_seconds` | `APE_OCR__GOOGLE_TIMEOUT_SECONDS` | `30` | D | Per Vision call. |
| `ocr.google_max_attempts` | `APE_OCR__GOOGLE_MAX_ATTEMPTS` | `3` | D | Retries on Vision errors. |
| `ocr.bangla_min_ratio` | `APE_OCR__BANGLA_MIN_RATIO` | `0.10` | D | Unicode Bangla char ratio that auto-routes to Bangla OCR-first. Scans with no Unicode still need `ocr_lang=bn`. |
| `ocr.max_ocr_pages_per_document` | `APE_OCR__MAX_OCR_PAGES_PER_DOCUMENT` | `100` | D | Cost cap. Hosted example uses `200`. Extra pages skip OCR. |
| `ocr.min_text_chars` | `APE_OCR__MIN_TEXT_CHARS` | `20` | D | Native page below this is OCR-eligible. |
| `ocr.min_image_area_ratio` | `APE_OCR__MIN_IMAGE_AREA_RATIO` | `0.08` | D | Embedded image must cover this fraction of the page before per-image OCR. |
| `ocr.dpi` | `APE_OCR__DPI` | `200` | D | Rasterization DPI. Higher = better OCR, more CPU/RAM. 150–300 typical. |
| `ocr.min_page_confidence` | `APE_OCR__MIN_PAGE_CONFIDENCE` | `0.3` | D | Discard OCR page below this confidence. |

**Backend options:** `noop` = never OCR (tests). `paddle` = local general OCR
(`requirements/ocr.txt`). `google_vision` = hosted / Bangla.

---

## 9. Embeddings (RAG — index identity)

Deployment-wide vector space. **Not** Project-overridable. Live `APE_EMBEDDING__*`
is the **target for the next rebuild**. Search uses the **active index build**.

Changing backend, model, or dimensions → bump `retrieval.embedding_set_version`,
rebuild, activate. Keep the old provider key until rollback is retired.

| Path | Env | Default | Scope | Meaning and options |
| ---- | --- | ------- | ----- | ------------------- |
| `embedding.backend` | `APE_EMBEDDING__BACKEND` | `hash` | D | `hash` (tests) \| `ollama` \| `openai` \| `gemini` \| `cohere`. Production forbids `hash`. `hosted_managed` requires `cohere`. |
| `embedding.model` | `APE_EMBEDDING__MODEL` | `text-embedding-3-large` | D | Model id. Hosted managed: `embed-v4.0`. Must match dimensions. |
| `embedding.dimensions` | `APE_EMBEDDING__DIMENSIONS` | `1024` | D | pgvector width. Must match `chunk_embeddings`. Some models have a fixed width (ada-002=1536, nomic-embed-text=768). |
| `embedding.batch_size` | `APE_EMBEDDING__BATCH_SIZE` | `32` | D | Texts per provider call during index. Higher = faster, more RAM/rate-limit risk. |
| `embedding.ollama_base_url` | `APE_EMBEDDING__OLLAMA_BASE_URL` | `http://localhost:11434` | D | Ollama embeddings. |
| `embedding.openai_api_key` | `APE_EMBEDDING__OPENAI_API_KEY` | `null` | D | Required for `openai` embeddings (`hosted_openai`). Separate from the LLM key. |
| `embedding.openai_base_url` | `APE_EMBEDDING__OPENAI_BASE_URL` | `https://api.openai.com` | D | OpenAI-compatible embed endpoint. |
| `embedding.gemini_api_key` | `APE_EMBEDDING__GEMINI_API_KEY` | `null` | D | Required for `gemini`. |
| `embedding.gemini_base_url` | `APE_EMBEDDING__GEMINI_BASE_URL` | Gemini v1beta URL | D | Gemini embed endpoint. |
| `embedding.provider_version` | `APE_EMBEDDING__PROVIDER_VERSION` | `1` | D | Provenance stamp, not a vendor API version. |

**Example:** OpenAI 1024-d set (esv=2) → Cohere `embed-v4.0` 1024-d (esv=3): set
Cohere target + key, bump esv, `reembed`, activate. Queries keep using OpenAI
until activation.

---

## 10. Retrieval pipeline (RAG — search)

Hybrid dense + BM25 + RRF is the production path. Candidate pool / HNSW / RRF
change **who enters the shortlist**. They do not change embeddings. Diversity
caps are **soft**; content-hash dedup is **hard**.

| Path | Env | Default | Scope | Meaning and options |
| ---- | --- | ------- | ----- | ------------------- |
| `retrieval.auto_embed` | `APE_RETRIEVAL__AUTO_EMBED` | `true` | D | After chunking, stage a full index build. `false` = stay `chunked` until manual embed. |
| `retrieval.auto_index` | `APE_RETRIEVAL__AUTO_INDEX` | `true` | D | Compatibility with the same full-build path (embed+index together). |
| `retrieval.default_top_k` | `APE_RETRIEVAL__DEFAULT_TOP_K` | `10` | D+P+Req | Final hits returned / offered to chat. Project `retrieval.top_k`. Request `top_k` capped by `ai_policy.max_request_top_k`. |
| `retrieval.score_threshold` | `APE_RETRIEVAL__SCORE_THRESHOLD` | `null` | D | Optional **cosine** floor at SQL (`distance ≤ 1-threshold`). `null` = no SQL cut. **Not** the chat evidence gate. |
| `retrieval.embedding_set_version` | `APE_RETRIEVAL__EMBEDDING_SET_VERSION` | `2` | D | Representation id. Local/hash=2; hosted Cohere=3. Bump when provider/model changes. |
| `retrieval.filterable_metadata_keys` | `APE_RETRIEVAL__FILTERABLE_METADATA_KEYS` | `source,tags,ocr_confidence` | D | Only these keys become SQL filters from the API. Add a key only if it is stored on chunks. |
| `retrieval.strategy` | `APE_RETRIEVAL__STRATEGY` | `hybrid` | D+P+Req | `semantic` \| `hybrid`. Production requires `hybrid`. Must be in `ai_policy.enabled_retrieval_strategies`. |
| `retrieval.semantic_candidate_top_k` | `APE_RETRIEVAL__SEMANTIC_CANDIDATE_TOP_K` | `50` | D | Dense candidates **before** RRF. Raise if the right chunk is missing entirely; costs latency. |
| `retrieval.hnsw_ef_search` | `APE_RETRIEVAL__HNSW_EF_SEARCH` | `100` | D | pgvector HNSW search effort. Higher = better recall, slower. Benchmark before changing. |
| `retrieval.keyword_candidate_top_k` | `APE_RETRIEVAL__KEYWORD_CANDIDATE_TOP_K` | `50` | D | BM25 candidates before RRF. Raise for exact citations, codes, statute numbers. |
| `retrieval.rrf_k` | `APE_RETRIEVAL__RRF_K` | `60` | D | RRF smoothing `1/(k+rank)`. Lower k → top ranks dominate more. 60 is the standard constant. |
| `retrieval.semantic_weight` | `APE_RETRIEVAL__SEMANTIC_WEIGHT` | `1.0` | D | RRF weight on dense ranks. Raise if paraphrases matter more than exact tokens. |
| `retrieval.keyword_weight` | `APE_RETRIEVAL__KEYWORD_WEIGHT` | `1.0` | D | RRF weight on BM25 ranks. Raise for identifiers / legal citations. |
| `retrieval.rerank_enabled` | `APE_RETRIEVAL__RERANK_ENABLED` | `true` | D+P Legacy | `false` forces rerank **off**. Prefer `rerank_mode`. Production requires the stage enabled. |
| `retrieval.rerank_mode` | `APE_RETRIEVAL__RERANK_MODE` | `always` | D+P | See rerank-mode table. Project Inherit / Always / Cross-language / Off. |
| `retrieval.rerank_top_n` | `APE_RETRIEVAL__RERANK_TOP_N` | `20` | D+P | With `rerank_candidate_window`, window sent to the reranker = `max(window, top_n, top_k)`. |
| `retrieval.rerank_candidate_window` | `APE_RETRIEVAL__RERANK_CANDIDATE_WINDOW` | `25` | D+P | Preferred window size into the reranker. |
| `retrieval.rerank_return_n` | `APE_RETRIEVAL__RERANK_RETURN_N` | `8` | D+P | Keep this many after rerank (then diversity caps). Lower = tighter context, more misses. |
| `retrieval.rerank_score_threshold` | `APE_RETRIEVAL__RERANK_SCORE_THRESHOLD` | `null` | D+P | Drop reranked hits below this **ranking** score. `null` = keep all returned. Separate from evidence bars. |
| `retrieval.reranker_backend` | `APE_RETRIEVAL__RERANKER_BACKEND` | `noop` | D | Occupant of the rerank stage. See backend table. Missing Cohere key **degrades** to RRF; does not block startup. |
| `retrieval.language_metadata_schema_version` | `APE_RETRIEVAL__LANGUAGE_METADATA_SCHEMA_VERSION` | `2026-08-18.v1` | D | Language-inventory schema id on builds. Bump with inventory shape changes. |
| `retrieval.fts_regconfig` | `APE_RETRIEVAL__FTS_REGCONFIG` | `simple` | D | Postgres FTS config. Keep `simple` for multilingual (no English stemming on Bangla). |
| `retrieval.min_ocr_confidence` | `APE_RETRIEVAL__MIN_OCR_CONFIDENCE` | `null` | D | Optional keyword-index filter. `null` = include all. Example `0.5` drops low-OCR chunks from BM25. |
| `retrieval.max_chunks_per_document` | `APE_RETRIEVAL__MAX_CHUNKS_PER_DOCUMENT` | `4` | D | Soft diversity: max chunks from one document in the final list. |
| `retrieval.max_chunks_per_section` | `APE_RETRIEVAL__MAX_CHUNKS_PER_SECTION` | `2` | D | Soft diversity per section. Prevents one heading from filling `top_k`. |
| `retrieval.deduplicate_by_content_hash` | `APE_RETRIEVAL__DEDUPLICATE_BY_CONTENT_HASH` | `true` | D | Hard drop of identical normalized text. Leave on. |
| `retrieval.passage_scoring_enabled` | `APE_RETRIEVAL__PASSAGE_SCORING_ENABLED` | `false` | D+P | Always-on windowed cosine on the fused list. Off by default (latency). Grounding can still **adaptively** passage-score a few high-confidence near-misses. Enable for eval/debug, not casually in prod. |
| `retrieval.passage_window_tokens` | `APE_RETRIEVAL__PASSAGE_WINDOW_TOKENS` | `96` | D+P | Passage window size. Must be > overlap and ≥ min. |
| `retrieval.passage_overlap_tokens` | `APE_RETRIEVAL__PASSAGE_OVERLAP_TOKENS` | `24` | D+P | Slide step. Must be < window. |
| `retrieval.passage_min_tokens` | `APE_RETRIEVAL__PASSAGE_MIN_TOKENS` | `32` | D+P | Ignore tiny windows. Must be ≤ window. |
| `retrieval.modifies_expansion_enabled` | `APE_RETRIEVAL__MODIFIES_EXPANSION_ENABLED` | `false` | D+P Legacy | `true` means expand **only if** mode is `off`. **Mode wins when not `off`.** |
| `retrieval.modifies_expansion_mode` | `APE_RETRIEVAL__MODIFIES_EXPANSION_MODE` | `off` | D+P | Incoming `MODIFIES` edges (amending sources). See table. Root `.env.example` sets `expand` — that **enables** expansion even if the boolean is false. |
| `retrieval.max_related_sources` | `APE_RETRIEVAL__MAX_RELATED_SOURCES` | `8` | D+P | Cap on related sources pulled via MODIFIES. |
| `retrieval.max_relationship_candidates` | `APE_RETRIEVAL__MAX_RELATIONSHIP_CANDIDATES` | `20` | D+P | Cap on extra candidates from those sources. |

**`retrieval.strategy`**

| Option | Path | Effect |
| ------ | ---- | ------ |
| `semantic` | dense only | Paraphrase/meaning. Misses exact tokens the embedder underweights. |
| `hybrid` | dense + BM25 + RRF (+ optional translate/rerank) | Production. Best default. |

**`rerank_mode`**

| Option | Effect |
| ------ | ------ |
| `always` | Paid/local rerank after RRF on every query. Platform default. |
| `cross_language` | Skip rerank when inventory says query language = corpus language. Saves cost. |
| `off` | RRF (or dense) order is final. `noop` backend with mode always is also pass-through. |

**`reranker_backend`**

| Option | Effect | Cost |
| ------ | ------ | ---- |
| `noop` | Pass-through; reports `rerank_status=passthrough`. Local/tests. | Free |
| `lexical` | Heuristic token overlap. Eval candidate. | Free |
| `embedding` | Cosine rerank of whole chunk. Eval candidate. | Embed calls |
| `embedding_max` | Passage-max embedding rerank. Eval candidate. | More embed calls |
| `cohere` | `rerank-v4.0-pro`. Hosted managed. Failure → RRF + cosine evidence. | Paid |

**`modifies_expansion_mode`**

| Option | Effect |
| ------ | ------ |
| `off` | Do not follow incoming MODIFIES (code default). |
| `observe` | Diagnose related sources; **do not** add them to recall. |
| `expand` | Search related amending sources (bounded). Needs `source_policy` to make suppression useful. |

---

## 11. Query translation (RAG — multilingual search)

Query-only rewrite. Original dense + BM25 **always** run. Failure degrades to
the original query. Off by default so hash/echo stacks need no LLM key.

| Path | Env | Default | Scope | Meaning |
| ---- | --- | ------- | ----- | ------- |
| `query_translation.enabled` | `APE_QUERY_TRANSLATION__ENABLED` | `false` | D+P | Project Inherit / On / Off. Runs only if the active build inventory has a **different** supported language than the query. |
| `query_translation.backend` | `APE_QUERY_TRANSLATION__BACKEND` | `openai` | D | LLM for rewrite. Cannot be `echo`. Uses `APE_LLM__OPENAI_API_KEY` when OpenAI. |
| `query_translation.model` | `APE_QUERY_TRANSLATION__MODEL` | `gpt-5-nano` | D | Cheap translator. Does **not** inherit `llm.model`. |
| `query_translation.prompt_version` | `APE_QUERY_TRANSLATION__PROMPT_VERSION` | `retrieval-translation-v2` | D | Prompt pin. |
| `query_translation.min_output_tokens` | `APE_QUERY_TRANSLATION__MIN_OUTPUT_TOKENS` | `256` | D | Provider completion floor. |
| `query_translation.max_output_tokens` | `APE_QUERY_TRANSLATION__MAX_OUTPUT_TOKENS` | `4096` | D | Cap. |
| `query_translation.request_timeout_seconds` | `APE_QUERY_TRANSLATION__REQUEST_TIMEOUT_SECONDS` | `45` | D | Translate timeout. |
| `query_translation.retry_max_attempts` | `APE_QUERY_TRANSLATION__RETRY_MAX_ATTEMPTS` | `1` | D | Extra tries. `0` = single shot. |
| `query_translation.target_languages` | `APE_QUERY_TRANSLATION__TARGET_LANGUAGES` | `bn,en` | D | Languages it may write into. Max useful set is the corpus pair. |

**Example:** English query, Bangla corpus, enabled → one Bangla rewrite added as
an extra hybrid branch. Citations still use original chunk text.

---

## 12. Cohere and reranker provider

Shared credential for embed + rerank. Models stay on embedding/reranker fields.

| Path | Env | Default | Scope | Meaning |
| ---- | --- | ------- | ----- | ------- |
| `cohere.api_key` | `APE_COHERE__API_KEY` | `null` | D | **Canonical** secret. Required for Cohere embeddings. Rerank missing key degrades. |
| `cohere.base_url` | `APE_COHERE__BASE_URL` | `https://api.cohere.com` | D | Canonical base URL. |
| `reranker.cohere_api_key` | `APE_RERANKER__COHERE_API_KEY` | `null` | D Legacy | Fallback if `APE_COHERE__API_KEY` empty. |
| `reranker.cohere_base_url` | `APE_RERANKER__COHERE_BASE_URL` | `https://api.cohere.com` | D Legacy | Fallback if canonical URL is still the default. |
| `reranker.cohere_model` | `APE_RERANKER__COHERE_MODEL` | `rerank-v4.0-pro` | D | Hosted managed rerank model. |
| `reranker.request_timeout_seconds` | `APE_RERANKER__REQUEST_TIMEOUT_SECONDS` | `10` | D | Rerank HTTP timeout. Hosted example uses `30`. |
| `reranker.provider_version` | `APE_RERANKER__PROVIDER_VERSION` | `1` | D | Provenance stamp. |

Resolved key: `cohere.api_key` then `reranker.cohere_api_key`.

---

## 13. LLM (generation)

Chat/generation provider. Hosted profiles require OpenAI. Tests use `echo`
(echoes the prompt shape; **not** product quality).

| Path | Env | Default | Scope | Meaning and options |
| ---- | --- | ------- | ----- | ------------------- |
| `llm.backend` | `APE_LLM__BACKEND` | `echo` | D+P (`llm.provider`) | `echo` \| `openai` \| `openai_compatible` \| `ollama` \| `gemini`. Production forbids `echo`. `hosted_managed` / `hosted_openai` require `openai`. |
| `llm.model` | `APE_LLM__MODEL` | `gpt-4o-mini` | D+P | Hosted managed chat: `gpt-5.6-luna`. Does not drive query translation. |
| `llm.temperature` | `APE_LLM__TEMPERATURE` | `null` | D+P | Omit = provider default. Some models reject temperature (stripped as `provider_safe_omission`). Lower = more deterministic RAG answers. |
| `llm.max_tokens` | `APE_LLM__MAX_TOKENS` | `4096` | D+P | Completion cap. Raise for long answers; cost/latency. |
| `llm.request_timeout_seconds` | `APE_LLM__REQUEST_TIMEOUT_SECONDS` | `120` | D | Chat HTTP timeout. |
| `llm.ollama_base_url` | `APE_LLM__OLLAMA_BASE_URL` | `http://localhost:11434` | D | Ollama chat. |
| `llm.openai_api_key` | `APE_LLM__OPENAI_API_KEY` | `null` | D | Required for OpenAI LLM, OpenAI query translation, and inherited web search. |
| `llm.openai_base_url` | `APE_LLM__OPENAI_BASE_URL` | `https://api.openai.com` | D | OpenAI-compatible chat endpoint. |
| `llm.gemini_api_key` | `APE_LLM__GEMINI_API_KEY` | `null` | D | Required for Gemini chat. |
| `llm.gemini_base_url` | `APE_LLM__GEMINI_BASE_URL` | Gemini v1beta URL | D | Gemini chat endpoint. |
| `llm.provider_version` | `APE_LLM__PROVIDER_VERSION` | `1` | D | Provenance stamp. |

---

## 14. Web search (chat response modes)

Used **only** when chat `response_mode` is not `indexed_only`. Credentials stay
deployment-owned. Omitted backend/model/key inherit compatible OpenAI LLM
settings. `disabled` is an explicit kill switch.

| Path | Env | Default | Scope | Meaning |
| ---- | --- | ------- | ----- | ------- |
| `web_search.backend` | `APE_WEB_SEARCH__BACKEND` | `null` (inherit) | D | `null` → OpenAI if LLM is OpenAI, else disabled. `openai` \| `disabled`. |
| `web_search.model` | `APE_WEB_SEARCH__MODEL` | `null` (inherit LLM/Project model) | D+P | Search model override. |
| `web_search.max_results` | `APE_WEB_SEARCH__MAX_RESULTS` | `8` | D+P | Provider result cap. |
| `web_search.max_evidence_chars` | `APE_WEB_SEARCH__MAX_EVIDENCE_CHARS` | `12000` | D+P | Web evidence budget in the prompt. |
| `web_search.max_output_tokens` | `APE_WEB_SEARCH__MAX_OUTPUT_TOKENS` | `4096` | D+P | Search call output cap. |
| `web_search.request_timeout_seconds` | `APE_WEB_SEARCH__REQUEST_TIMEOUT_SECONDS` | `45` | D+P | Search timeout. |
| `web_search.openai_api_key` | `APE_WEB_SEARCH__OPENAI_API_KEY` | `null` | D | Dedicated search key; else `APE_LLM__OPENAI_API_KEY`. |
| `web_search.openai_base_url` | `APE_WEB_SEARCH__OPENAI_BASE_URL` | `null` | D | Else LLM OpenAI base URL. |
| `web_search.provider_version` | `APE_WEB_SEARCH__PROVIDER_VERSION` | `responses-web-search-v1` | D | Pin of the Responses `web_search` integration. |

---

## 15. Chat / grounding (RAG — answers)

This is the **product behavior** layer. Retrieval can be correct and chat still
refuse (gate) or over-answer (observe + low bars). Thresholds are
**provider-calibrated**, not universal constants. Recalibrate in Test Lab after
embedding/reranker/corpus change.

Code defaults below. Hosted `.env.example` currently sets
`candidate_wise_grounding_enabled=true` and `grounding_mode=balanced`.

| Path | Env | Default | Scope | Meaning and options |
| ---- | --- | ------- | ----- | ------------------- |
| `chat.response_mode` | `APE_CHAT__RESPONSE_MODE` | `indexed_only` | D+P | See response-mode table. Web modes need v5 prompt + web provider + credentials. |
| `chat.retrieval_top_k` | `APE_CHAT__RETRIEVAL_TOP_K` | `10` | D | Chat fetch size. Project overlay copies `retrieval.top_k` here. Duplicate of `retrieval.default_top_k` at deployment. |
| `chat.max_context_chunks` | `APE_CHAT__MAX_CONTEXT_CHUNKS` | `8` | D+P | Max chunks (or admitted units) in the prompt. |
| `chat.context_char_budget` | `APE_CHAT__CONTEXT_CHAR_BUDGET` | `12000` | D+P | Character budget. Admitted `EvidenceUnit`s are **omitted**, never truncated. Legacy chunks may be sliced. |
| `chat.max_history_messages` | `APE_CHAT__MAX_HISTORY_MESSAGES` | `20` | D+P | Prior turns kept. `0` = stateless. |
| `chat.system_prompt_version` | `APE_CHAT__SYSTEM_PROMPT_VERSION` | `v5` | D+P (`prompt_version`) | Prompt pin. Web modes **require** `v5`. |
| `chat.include_citations` | `APE_CHAT__INCLUDE_CITATIONS` | `true` | D+P | Attach citations on the assistant message. |
| `chat.citation_excerpt_max_chars` | `APE_CHAT__CITATION_EXCERPT_MAX_CHARS` | `200` | D+P | Excerpt length in citation payloads. |
| `chat.evidence_score_mode` | `APE_CHAT__EVIDENCE_SCORE_MODE` | `whole_chunk` | D+P | `whole_chunk` = chunk cosine. `passage_max` = best passage window; requires passage scoring **and** hybrid. |
| `chat.evidence_gate_mode` | `APE_CHAT__EVIDENCE_GATE_MODE` | `enforce` | D+P | `enforce` = refuse LLM when evidence fails. `observe` = still generate; record the decision (measure in Test Lab only). |
| `chat.minimum_semantic_evidence_score` | `APE_CHAT__MINIMUM_SEMANTIC_EVIDENCE_SCORE` | `0.35` | D+P (`retrieval.evidence_score_threshold`) | Cosine admit bar when **no** true reranker applied. Calibrated ~0.35 on OpenAI 1024-d; **re-measure** on Cohere. Project values `< 0.15` are ignored as leftover RRF-scale. |
| `chat.lexical_corroboration_floor_score` | `APE_CHAT__LEXICAL_CORROBORATION_FLOOR_SCORE` | `0.30` | D+P | Same-language rescue: semantic may sit between floor and 0.35 if token overlap is strong (OCR/tables). Must be ≤ semantic bar. |
| `chat.lexical_corroboration_coverage` | `APE_CHAT__LEXICAL_CORROBORATION_COVERAGE` | `0.50` | D+P | Fraction of query tokens that must appear in the chunk for lexical rescue. |
| `chat.cross_language_semantic_evidence_score_threshold` | `APE_CHAT__CROSS_LANGUAGE_SEMANTIC_EVIDENCE_SCORE_THRESHOLD` | `0.30` | D+P | Cross-language cosine bar (independent of the lexical floor). Must be ≤ semantic bar. |
| `chat.minimum_reranker_evidence_score` | `APE_CHAT__MINIMUM_RERANKER_EVIDENCE_SCORE` | `0.40` | D+P | When rerank **applied**, this relevance is the medium admit bar. Cosine is then corroboration, not the primary score. |
| `chat.high_confidence_reranker_evidence_score` | `APE_CHAT__HIGH_CONFIDENCE_RERANKER_EVIDENCE_SCORE` | `0.70` | D+P | Must be **>** medium bar. Used by `balanced` near-miss admission and passage rescue. |
| `chat.grounding_mode` | `APE_CHAT__GROUNDING_MODE` | `strict` | D+P | See grounding table. |
| `chat.candidate_wise_grounding_enabled` | `APE_CHAT__CANDIDATE_WISE_GROUNDING_ENABLED` | `false` | D+P | `true` = admitted `EvidenceUnit`s drive the prompt. `false` = assessments are shadow; legacy gate still decides. Hosted example turns this **on**. |
| `chat.minimum_evidence_score` | `APE_CHAT__MINIMUM_EVIDENCE_SCORE` | unset | — | **Removed.** Setting it **fails startup**. |
| `chat.minimum_query_token_coverage` | `APE_CHAT__MINIMUM_QUERY_TOKEN_COVERAGE` | unset | P Legacy | Deployment env **fails startup** if set. Project field maps to lexical coverage; values **below** global coverage are ignored. |
| `chat.minimum_claim_token_coverage` | `APE_CHAT__MINIMUM_CLAIM_TOKEN_COVERAGE` | `0.35` | D+P | After generation: claim vs evidence token overlap to mark supported. |
| `chat.minimum_claim_semantic_score` | `APE_CHAT__MINIMUM_CLAIM_SEMANTIC_SCORE` | `0.25` | D | Cross-language claim cosine → supported. |
| `chat.claim_semantic_reject_floor` | `APE_CHAT__CLAIM_SEMANTIC_REJECT_FLOOR` | `0.15` | D | Below → unsupported; between floor and min → unverified. Must be ≤ min claim semantic. |
| `chat.insufficient_evidence_message` | `APE_CHAT__INSUFFICIENT_EVIDENCE_MESSAGE` | canned refusal | D | User-visible refuse text when the gate blocks. |
| `chat.auto_title_max_chars` | `APE_CHAT__AUTO_TITLE_MAX_CHARS` | `80` | D | Conversation auto-title length. |

**`response_mode`**

| Option | Behavior |
| ------ | -------- |
| `indexed_only` | Knowledge RAG only (default). |
| `indexed_then_web` | Web search **only after** the knowledge gate says insufficient. Scoped/time/metadata queries never fall back. |
| `indexed_and_web` | Knowledge + web in one turn. Citations stay in separate families. |

**`grounding_mode`**

| Option | Behavior |
| ------ | -------- |
| `strict` | Calibrated reranker hit still needs an independent semantic, lexical, or cross-language signal. High assurance. |
| `balanced` | May **add** a high-confidence reranker hit (`≥ 0.70`) that only narrowly misses corroboration. Cannot admit low-confidence hits or drop a strict pass. |

**`evidence_gate_mode`:** `enforce` is the product. `observe` is a measurement
mode — the model still talks, so you will not see refusals.

**Worked example (same-language OCR table):** cosine 0.32, query-token coverage
0.70, no reranker → fails 0.35 but passes lexical rescue (floor 0.30 + coverage
0.50) → admitted. Cross-language hit with coverage ~0 still refuses.

---

## 16. Contextual generation (`/generations`)

Caller-supplied context, not RAG. Separate trust boundary (ADR-019).

| Path | Env | Default | Scope | Meaning |
| ---- | --- | ------- | ----- | ------- |
| `generation.max_request_bytes` | `APE_GENERATION__MAX_REQUEST_BYTES` | `262144` | D | Whole request cap. |
| `generation.max_context_bytes` | `APE_GENERATION__MAX_CONTEXT_BYTES` | `204800` | D | Caller context cap. |
| `generation.max_schema_bytes` | `APE_GENERATION__MAX_SCHEMA_BYTES` | `32768` | D | JSON schema cap. |
| `generation.max_context_depth` | `APE_GENERATION__MAX_CONTEXT_DEPTH` | `12` | D | Nested context depth. |
| `generation.max_context_nodes` | `APE_GENERATION__MAX_CONTEXT_NODES` | `5000` | D | Nested node cap (DoS guard). |
| `generation.default_retention` | `APE_GENERATION__DEFAULT_RETENTION` | `none` | D | `none` \| `metadata_only` \| `full` — what is stored from the caller payload. |
| `generation.allow_full_retention` | `APE_GENERATION__ALLOW_FULL_RETENTION` | `true` | D | If `false`, callers cannot request `full`. |

---

## 17. Evaluation acceptance

Quality-run **pass/fail bars**, not runtime retrieval. Changing them does not
change chat; it changes whether an eval job is accepted.

| Path | Env | Default | Scope | Meaning |
| ---- | --- | ------- | ----- | ------- |
| `evaluation.evaluator_version` | `APE_EVALUATION__EVALUATOR_VERSION` | `quality-v3` | D | Metric set pin. |
| `evaluation.default_top_k` | `APE_EVALUATION__DEFAULT_TOP_K` | `5` | D | Recall@k default. |
| `evaluation.max_cases_per_dataset` | `APE_EVALUATION__MAX_CASES_PER_DATASET` | `500` | D | Dataset size cap. |
| `evaluation.minimum_recall_at_k` | `APE_EVALUATION__MINIMUM_RECALL_AT_K` | `0.80` | D | Accept bar. |
| `evaluation.minimum_rank_1_accuracy` | `APE_EVALUATION__MINIMUM_RANK_1_ACCURACY` | `0.80` | D | Accept bar. |
| `evaluation.minimum_cross_lingual_recall_at_k` | `APE_EVALUATION__MINIMUM_CROSS_LINGUAL_RECALL_AT_K` | `0.75` | D | Accept bar. |
| `evaluation.minimum_filtered_correctness` | `APE_EVALUATION__MINIMUM_FILTERED_CORRECTNESS` | `0.95` | D | Metadata-filter cases. |
| `evaluation.maximum_false_refusal_rate` | `APE_EVALUATION__MAXIMUM_FALSE_REFUSAL_RATE` | `0.10` | D | Over-refusing answerable cases. |
| `evaluation.maximum_false_accept_rate` | `APE_EVALUATION__MAXIMUM_FALSE_ACCEPT_RATE` | `0.0` | D | Answering no-answer cases. |
| `evaluation.maximum_accepted_without_relevant_evidence_rate` | `APE_EVALUATION__MAXIMUM_ACCEPTED_WITHOUT_RELEVANT_EVIDENCE_RATE` | `0.0` | D | Gate passed but gold chunk absent. |
| `evaluation.minimum_groundedness` | `APE_EVALUATION__MINIMUM_GROUNDEDNESS` | `0.80` | D | Claim support bar. |
| `evaluation.minimum_citation_coverage` | `APE_EVALUATION__MINIMUM_CITATION_COVERAGE` | `0.80` | D | Numbered citation bar. |
| `evaluation.maximum_p95_latency_ms` | `APE_EVALUATION__MAXIMUM_P95_LATENCY_MS` | `750` | D | Retrieval p95 accept (may be tight for Cohere+translate). |
| `evaluation.maximum_metric_regression` | `APE_EVALUATION__MAXIMUM_METRIC_REGRESSION` | `0.02` | D | Allowed drop vs last successful run. |
| `evaluation.minimum_reranker_ndcg_gain` | `APE_EVALUATION__MINIMUM_RERANKER_NDCG_GAIN` | `0.02` | D | Rerank comparison gain. |
| `evaluation.maximum_reranker_latency_penalty_ms` | `APE_EVALUATION__MAXIMUM_RERANKER_LATENCY_PENALTY_MS` | `150` | D | Extra latency allowed for rerank. |
| `evaluation.reranker_candidates` | `APE_EVALUATION__RERANKER_CANDIDATES` | `lexical,embedding,embedding_max` | D | Backends compared in a run (plus the live backend). |

---

## 18. AI policy (deployment safety around Project config)

Bounds. Projects cannot raise a cap the deployment lowered.

| Path | Env | Default | Scope | Meaning and options |
| ---- | --- | ------- | ----- | ------------------- |
| `ai_policy.request_override_mode` | `APE_AI_POLICY__REQUEST_OVERRIDE_MODE` | `compatibility` | D | `compatibility` = log deprecated request LLM/prompt/rerank fields and apply them. `strict` = `request_policy_override_forbidden`. Move to `strict` once clients stop sending those fields. |
| `ai_policy.max_request_top_k` | `APE_AI_POLICY__MAX_REQUEST_TOP_K` | `100` | D | Hard cap on request `top_k`. |
| `ai_policy.source_policy_mode` | `APE_AI_POLICY__SOURCE_POLICY_MODE` | `off` | D+P | Default inherited by Projects. Hosted example: `enforce`. See source-policy table. |
| `ai_policy.source_policy_deployment_cap` | `APE_AI_POLICY__SOURCE_POLICY_DEPLOYMENT_CAP` | `enforce` | D | Emergency **maximum**. Can only **lower** Project/global mode. Cannot turn enforce on if the Project is `off`. |
| `ai_policy.enabled_retrieval_strategies` | `APE_AI_POLICY__ENABLED_RETRIEVAL_STRATEGIES` | `semantic,hybrid` | D | Allowlist. A Project/request strategy outside this list is rejected. |

**`source_policy_mode`**

| Option | Effect |
| ------ | ------ |
| `off` | No applicability/suppression (code default). |
| `observe` | Record what would be suppressed; do not hide. |
| `enforce` | Hide superseded sources according to source generation / MODIFIES. |

---

## 19. Auth

Single on/off. When `false`, protected routes skip checks (dev/tests).
Production **requires** `true` plus 32-byte secrets.

| Path | Env | Default | Scope | Meaning |
| ---- | --- | ------- | ----- | ------- |
| `auth.enabled` | `APE_AUTH__ENABLED` | `false` | D | Org API keys + Super Admin session. |
| `auth.key_pepper` | `APE_AUTH__KEY_PEPPER` | `null` | D | HMAC pepper for API keys. ≥ 32 bytes. |
| `auth.verify_cache_enabled` | `APE_AUTH__VERIFY_CACHE_ENABLED` | `true` | D | Cache verified keys. Revoke can lag up to TTL. |
| `auth.verify_cache_ttl_seconds` | `APE_AUTH__VERIFY_CACHE_TTL_SECONDS` | `60` | D | Cache TTL. Hosted example `300`. Lower = faster revoke, more Redis. |
| `auth.verify_cache_backend` | `APE_AUTH__VERIFY_CACHE_BACKEND` | `redis` | D | `redis` (prod) \| `memory` (single-process local). |
| `auth.rate_limit_enabled` | `APE_AUTH__RATE_LIMIT_ENABLED` | `true` | D | Org-key rate limit. |
| `auth.rate_limit_requests` | `APE_AUTH__RATE_LIMIT_REQUESTS` | `1000` | D | Max requests per window. |
| `auth.rate_limit_window_seconds` | `APE_AUTH__RATE_LIMIT_WINDOW_SECONDS` | `60` | D | Window length. |
| `auth.rate_limit_fail_open` | `APE_AUTH__RATE_LIMIT_FAIL_OPEN` | `false` | D | If Redis is down: `true` allows traffic (dev); `false` rejects (prod). |
| `auth.admin_login_rate_limit_requests` | `APE_AUTH__ADMIN_LOGIN_RATE_LIMIT_REQUESTS` | `5` | D | Console login attempts per window. |
| `auth.admin_login_rate_limit_window_seconds` | `APE_AUTH__ADMIN_LOGIN_RATE_LIMIT_WINDOW_SECONDS` | `60` | D | Login window. |
| `auth.admin_jwt_secret` | `APE_AUTH__ADMIN_JWT_SECRET` | `null` | D | Console JWT. ≥ 32 bytes. |
| `auth.admin_access_token_expire_minutes` | `APE_AUTH__ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | D | Access cookie TTL. |
| `auth.admin_refresh_token_expire_days` | `APE_AUTH__ADMIN_REFRESH_TOKEN_EXPIRE_DAYS` | `14` | D | Refresh cookie TTL. |
| `auth.admin_cookie_secure` | `APE_AUTH__ADMIN_COOKIE_SECURE` | `false` | D | Production requires `true`. Required if SameSite=`none`. |
| `auth.admin_cookie_samesite` | `APE_AUTH__ADMIN_COOKIE_SAMESITE` | `lax` | D | `lax` \| `strict` \| `none`. |
| `auth.admin_cookie_domain` | `APE_AUTH__ADMIN_COOKIE_DOMAIN` | `null` | D | Cookie domain. Blank = host-only. |

There is **no** `APE_AUTH__ADMIN_API_KEY` in current Settings (older plan docs
mention it). Super Admin bootstrap is the CLI user + JWT cookie.

---

## Project AI revision fields (sparse)

Operator Console / `POST .../ai-config`. Omitted = inherit. Console form exposes
a **subset**; unlisted stored fields are preserved on save.

| Project path | Inherits from | Notes |
| ------------ | ------------- | ----- |
| `llm.provider` | `llm.backend` | |
| `llm.model` / `temperature` / `max_tokens` | `llm.*` | |
| `retrieval.strategy` / `top_k` | `retrieval.strategy` / `default_top_k` | Request may still cap `top_k`. |
| `retrieval.rerank_mode` | `retrieval.rerank_mode` | Legacy `rerank_enabled` maps true→always, false→off. |
| `retrieval.rerank_top_n` / `rerank_score_threshold` / windows / `return_n` | same retrieval fields | |
| `retrieval.evidence_score_threshold` | `chat.minimum_semantic_evidence_score` | Ignores leftover `< 0.15`. |
| `retrieval.passage_scoring_*` | same | `passage_max` evidence mode requires this + hybrid. |
| `retrieval.query_translation_enabled` | `query_translation.enabled` | Backend/model stay deployment. |
| `retrieval.modifies_expansion_mode` | same | Legacy `modifies_expansion_enabled=true` → expand. |
| `retrieval.max_related_sources` / `max_relationship_candidates` | same | |
| `chat.response_mode` | `chat.response_mode` | Web needs provider + v5. |
| `chat.max_context_chunks` / `context_char_budget` / `max_history_messages` | same | |
| `chat.include_citations` / `citation_excerpt_max_chars` | same | |
| `chat.evidence_score_mode` / `evidence_gate_mode` | same | |
| `chat.lexical_corroboration_floor_score` | same | |
| `chat.cross_language_semantic_evidence_score_threshold` | same | |
| `chat.minimum_claim_token_coverage` | same | |
| `chat.minimum_reranker_evidence_score` / `high_confidence_reranker_evidence_score` | same | |
| `chat.grounding_mode` / `candidate_wise_grounding_enabled` | same | |
| `web_search.enabled` | inferred from resolved backend | Cannot add a provider the deployment did not configure. |
| `web_search.model` / `max_results` / budgets / timeout | `web_search.*` | |
| `domain_instructions` | `""` | Injected domain text. |
| `prompt_profile` | `default` | |
| `prompt_version` | `chat.system_prompt_version` | |
| `source_policy_mode` | `ai_policy.source_policy_mode` | Then clamped by deployment cap. |

**Never in Project policy:** embedding backend/model/dimensions, chunking,
parsing, OCR, storage, jobs, auth, eval bars, Cohere/LLM secrets, `embedding_set_version`.

---

## Compose-only keys (not Settings)

Docker Compose secrets and ports. The app still reads the `APE_*` mapping
Compose injects.

| Compose key | Becomes | Role |
| ----------- | ------- | ---- |
| `POSTGRES_USER` | `APE_DATABASE__USER` | DB user (and the Postgres container). |
| `POSTGRES_PASSWORD` | `APE_DATABASE__PASSWORD` | DB password. |
| `POSTGRES_DB` | `APE_DATABASE__NAME` | DB name. |
| `POSTGRES_PORT` | host port only | Publish Postgres to the host. App uses 5432 inside the network. |
| `REDIS_PASSWORD` | `APE_REDIS__PASSWORD` | Redis requirepass. |
| `REDIS_PORT` | host port only | |
| `MINIO_ROOT_USER` | `APE_MINIO__ACCESS_KEY` | |
| `MINIO_ROOT_PASSWORD` | `APE_MINIO__SECRET_KEY` | |
| `MINIO_BUCKET` | `APE_MINIO__BUCKET` | |
| `MINIO_API_PORT` / `MINIO_CONSOLE_PORT` | host ports only | |
| `VITE_API_ORIGIN` | frontend build | Browser API origin. Blank = same-origin via gateway. |

---

## Deprecated / do not set

| Env | What happens |
| --- | ------------ |
| `APE_CHAT__MINIMUM_EVIDENCE_SCORE` | Startup error. Use `APE_CHAT__MINIMUM_SEMANTIC_EVIDENCE_SCORE`. |
| `APE_CHAT__MINIMUM_QUERY_TOKEN_COVERAGE` | Startup error. Use `APE_CHAT__LEXICAL_CORROBORATION_COVERAGE`. |
| `APE_RETRIEVAL__RERANK_ENABLED` | Still works; `rerank_mode` is the real control. `false` forces off. |
| `APE_RETRIEVAL__MODIFIES_EXPANSION_ENABLED` | Mode wins when mode ≠ `off`. |
| `APE_RERANKER__COHERE_API_KEY` / `__COHERE_BASE_URL` | Fallback only. Prefer `APE_COHERE__*`. |
| `APE_RUNTIME__PROFILE=hosted_openai` | Supported but deprecated. |

ADR-004 still names a **platform** DB layer; it is not implemented. Effective
layers today: deployment → Project → request allowlist → safety.

---

## Env → path (quick index)

Every Settings field. Default is the **code** default.

| Env | Path | Default |
| --- | ---- | ------- |
| `APE_APP__NAME` | `app.name` | `AI Platform Engine` |
| `APE_APP__ENV` | `app.env` | `development` |
| `APE_APP__DEBUG` | `app.debug` | `true` |
| `APE_APP__VERSION` | `app.version` | `0.9.0` |
| `APE_APP__API_V1_PREFIX` | `app.api_v1_prefix` | `/api/v1` |
| `APE_SERVER__HOST` | `server.host` | `0.0.0.0` |
| `APE_SERVER__PORT` | `server.port` | `8000` |
| `APE_SERVER__RELOAD` | `server.reload` | `false` |
| `APE_SERVER__WORKERS` | `server.workers` | `1` |
| `APE_LOGGING__LEVEL` | `logging.level` | `INFO` |
| `APE_LOGGING__RENDER_JSON` | `logging.render_json` | `false` |
| `APE_RUNTIME__PROFILE` | `runtime.profile` | `development` |
| `APE_RUNTIME__STARTUP_TIMEOUT_SECONDS` | `runtime.startup_timeout_seconds` | `30` |
| `APE_RUNTIME__DEPENDENCY_TIMEOUT_SECONDS` | `runtime.dependency_timeout_seconds` | `3` |
| `APE_RUNTIME__PROVIDER_TIMEOUT_SECONDS` | `runtime.provider_timeout_seconds` | `15` |
| `APE_RUNTIME__WORKER_HEARTBEAT_SECONDS` | `runtime.worker_heartbeat_seconds` | `10` |
| `APE_RUNTIME__WORKER_STALE_SECONDS` | `runtime.worker_stale_seconds` | `35` |
| `APE_CORS__ALLOW_ORIGINS` | `cors.allow_origins` | `*` |
| `APE_CORS__ALLOW_CREDENTIALS` | `cors.allow_credentials` | `true` |
| `APE_CORS__ALLOW_METHODS` | `cors.allow_methods` | `*` |
| `APE_CORS__ALLOW_HEADERS` | `cors.allow_headers` | `*` |
| `APE_DATABASE__HOST` | `database.host` | `localhost` |
| `APE_DATABASE__PORT` | `database.port` | `5432` |
| `APE_DATABASE__USER` | `database.user` | `ape` |
| `APE_DATABASE__PASSWORD` | `database.password` | `ape` |
| `APE_DATABASE__NAME` | `database.name` | `ape` |
| `APE_DATABASE__ECHO` | `database.echo` | `false` |
| `APE_DATABASE__POOL_SIZE` | `database.pool_size` | `5` |
| `APE_DATABASE__MAX_OVERFLOW` | `database.max_overflow` | `10` |
| `APE_DATABASE__POOL_TIMEOUT` | `database.pool_timeout` | `30` |
| `APE_DATABASE__POOL_RECYCLE` | `database.pool_recycle` | `1800` |
| `APE_TEST_DATABASE__NAME` | `test_database.name` | `ape_test` |
| `APE_TEST_DATABASE__ALLOW_MIGRATIONS` | `test_database.allow_migrations` | `false` |
| `APE_REDIS__HOST` | `redis.host` | `localhost` |
| `APE_REDIS__PORT` | `redis.port` | `6379` |
| `APE_REDIS__DB` | `redis.db` | `0` |
| `APE_REDIS__PASSWORD` | `redis.password` | `null` |
| `APE_STORAGE__BACKEND` | `storage.backend` | `local` |
| `APE_STORAGE__LOCAL_ROOT` | `storage.local_root` | `./storage` |
| `APE_MINIO__ENDPOINT` | `minio.endpoint` | `localhost:9000` |
| `APE_MINIO__ACCESS_KEY` | `minio.access_key` | `minioadmin` |
| `APE_MINIO__SECRET_KEY` | `minio.secret_key` | `minioadmin` |
| `APE_MINIO__SECURE` | `minio.secure` | `false` |
| `APE_MINIO__REGION` | `minio.region` | `us-east-1` |
| `APE_MINIO__BUCKET` | `minio.bucket` | `ape-artifacts` |
| `APE_JOBS__BACKEND` | `jobs.backend` | `taskiq` |
| `APE_JOBS__DISPATCHER_ENABLED` | `jobs.dispatcher_enabled` | `true` |
| `APE_JOBS__DISPATCHER_POLL_SECONDS` | `jobs.dispatcher_poll_seconds` | `1` |
| `APE_JOBS__DISPATCHER_BATCH_SIZE` | `jobs.dispatcher_batch_size` | `50` |
| `APE_JOBS__LEASE_SECONDS` | `jobs.lease_seconds` | `300` |
| `APE_JOBS__HEARTBEAT_SECONDS` | `jobs.heartbeat_seconds` | `30` |
| `APE_JOBS__MAX_ATTEMPTS` | `jobs.max_attempts` | `3` |
| `APE_JOBS__RETRY_BASE_DELAY_SECONDS` | `jobs.retry_base_delay_seconds` | `2` |
| `APE_JOBS__RETRY_MAX_DELAY_SECONDS` | `jobs.retry_max_delay_seconds` | `300` |
| `APE_JOBS__DISPATCH_RETRY_BASE_SECONDS` | `jobs.dispatch_retry_base_seconds` | `1` |
| `APE_JOBS__DISPATCH_RETRY_MAX_SECONDS` | `jobs.dispatch_retry_max_seconds` | `60` |
| `APE_WEBHOOKS__ENABLED` | `webhooks.enabled` | `true` |
| `APE_WEBHOOKS__DISPATCHER_ENABLED` | `webhooks.dispatcher_enabled` | `true` |
| `APE_WEBHOOKS__SIGNING_KEY` | `webhooks.signing_key` | `development-only-webhook-signing-key` |
| `APE_WEBHOOKS__DISPATCHER_POLL_SECONDS` | `webhooks.dispatcher_poll_seconds` | `1` |
| `APE_WEBHOOKS__DISPATCHER_BATCH_SIZE` | `webhooks.dispatcher_batch_size` | `50` |
| `APE_WEBHOOKS__DELIVERY_TIMEOUT_SECONDS` | `webhooks.delivery_timeout_seconds` | `10` |
| `APE_WEBHOOKS__DELIVERY_LEASE_SECONDS` | `webhooks.delivery_lease_seconds` | `60` |
| `APE_WEBHOOKS__MAX_ATTEMPTS` | `webhooks.max_attempts` | `6` |
| `APE_WEBHOOKS__RETRY_BASE_SECONDS` | `webhooks.retry_base_seconds` | `5` |
| `APE_WEBHOOKS__RETRY_MAX_SECONDS` | `webhooks.retry_max_seconds` | `3600` |
| `APE_WEBHOOKS__RESPONSE_EXCERPT_CHARS` | `webhooks.response_excerpt_chars` | `1000` |
| `APE_KNOWLEDGE__MAX_UPLOAD_BYTES` | `knowledge.max_upload_bytes` | `52428800` |
| `APE_MALWARE_SCAN__BACKEND` | `malware_scan.backend` | `disabled` |
| `APE_MALWARE_SCAN__HOST` | `malware_scan.host` | `localhost` |
| `APE_MALWARE_SCAN__PORT` | `malware_scan.port` | `3310` |
| `APE_MALWARE_SCAN__TIMEOUT_SECONDS` | `malware_scan.timeout_seconds` | `15` |
| `APE_PARSING__PDF_TEXT_PARSERS` | `parsing.pdf_text_parsers` | `pymupdf,pdfium` |
| `APE_PARSING__MIN_PAGE_QUALITY_SCORE` | `parsing.min_page_quality_score` | `0.55` |
| `APE_PARSING__MIN_DOCUMENT_SUCCESS_RATIO` | `parsing.min_document_success_ratio` | `0.2` |
| `APE_PARSING__MIN_TEXT_CHARS` | `parsing.min_text_chars` | `20` |
| `APE_CHUNKING__STRATEGY` | `chunking.strategy` | `auto` |
| `APE_CHUNKING__TARGET_TOKENS` | `chunking.target_tokens` | `250` |
| `APE_CHUNKING__MAX_TOKENS` | `chunking.max_tokens` | `400` |
| `APE_CHUNKING__MIN_TOKENS` | `chunking.min_tokens` | `50` |
| `APE_CHUNKING__OVERLAP_TOKENS` | `chunking.overlap_tokens` | `50` |
| `APE_CHUNKING__STRUCTURE_SCORE_THRESHOLD` | `chunking.structure_score_threshold` | `0.55` |
| `APE_CHUNKING__LONG_BLOCK_TOKEN_THRESHOLD` | `chunking.long_block_token_threshold` | `600` |
| `APE_CHUNKING__SIMILARITY_DROP_THRESHOLD` | `chunking.similarity_drop_threshold` | `0.35` |
| `APE_CHUNKING__SEMANTIC_BATCH_SIZE` | `chunking.semantic_batch_size` | `32` |
| `APE_CHUNKING__CHUNKER_VERSION` | `chunking.chunker_version` | `3.0.0` |
| `APE_CHUNKING__TOKEN_COUNT_METHOD` | `chunking.token_count_method` | `unicode_property_v1` |
| `APE_CHUNKING__OCR_CONFIDENCE_THRESHOLD` | `chunking.ocr_confidence_threshold` | `0.5` |
| `APE_OCR__ENABLED` | `ocr.enabled` | `false` |
| `APE_OCR__BACKEND` | `ocr.backend` | `noop` |
| `APE_OCR__BANGLA_BACKEND` | `ocr.bangla_backend` | `noop` |
| `APE_OCR__LANG` | `ocr.lang` | `en` |
| `APE_OCR__USE_GPU` | `ocr.use_gpu` | `false` |
| `APE_OCR__GOOGLE_API_KEY` | `ocr.google_api_key` | `null` |
| `APE_OCR__GOOGLE_ENDPOINT` | `ocr.google_endpoint` | Vision annotate URL |
| `APE_OCR__GOOGLE_TIMEOUT_SECONDS` | `ocr.google_timeout_seconds` | `30` |
| `APE_OCR__GOOGLE_MAX_ATTEMPTS` | `ocr.google_max_attempts` | `3` |
| `APE_OCR__BANGLA_MIN_RATIO` | `ocr.bangla_min_ratio` | `0.10` |
| `APE_OCR__MAX_OCR_PAGES_PER_DOCUMENT` | `ocr.max_ocr_pages_per_document` | `100` |
| `APE_OCR__MIN_TEXT_CHARS` | `ocr.min_text_chars` | `20` |
| `APE_OCR__MIN_IMAGE_AREA_RATIO` | `ocr.min_image_area_ratio` | `0.08` |
| `APE_OCR__DPI` | `ocr.dpi` | `200` |
| `APE_OCR__MIN_PAGE_CONFIDENCE` | `ocr.min_page_confidence` | `0.3` |
| `APE_EMBEDDING__BACKEND` | `embedding.backend` | `hash` |
| `APE_EMBEDDING__MODEL` | `embedding.model` | `text-embedding-3-large` |
| `APE_EMBEDDING__DIMENSIONS` | `embedding.dimensions` | `1024` |
| `APE_EMBEDDING__BATCH_SIZE` | `embedding.batch_size` | `32` |
| `APE_EMBEDDING__OLLAMA_BASE_URL` | `embedding.ollama_base_url` | `http://localhost:11434` |
| `APE_EMBEDDING__OPENAI_API_KEY` | `embedding.openai_api_key` | `null` |
| `APE_EMBEDDING__OPENAI_BASE_URL` | `embedding.openai_base_url` | `https://api.openai.com` |
| `APE_EMBEDDING__GEMINI_API_KEY` | `embedding.gemini_api_key` | `null` |
| `APE_EMBEDDING__GEMINI_BASE_URL` | `embedding.gemini_base_url` | Gemini v1beta URL |
| `APE_EMBEDDING__PROVIDER_VERSION` | `embedding.provider_version` | `1` |
| `APE_RETRIEVAL__AUTO_EMBED` | `retrieval.auto_embed` | `true` |
| `APE_RETRIEVAL__AUTO_INDEX` | `retrieval.auto_index` | `true` |
| `APE_RETRIEVAL__DEFAULT_TOP_K` | `retrieval.default_top_k` | `10` |
| `APE_RETRIEVAL__SCORE_THRESHOLD` | `retrieval.score_threshold` | `null` |
| `APE_RETRIEVAL__EMBEDDING_SET_VERSION` | `retrieval.embedding_set_version` | `2` |
| `APE_RETRIEVAL__FILTERABLE_METADATA_KEYS` | `retrieval.filterable_metadata_keys` | `source,tags,ocr_confidence` |
| `APE_RETRIEVAL__STRATEGY` | `retrieval.strategy` | `hybrid` |
| `APE_RETRIEVAL__SEMANTIC_CANDIDATE_TOP_K` | `retrieval.semantic_candidate_top_k` | `50` |
| `APE_RETRIEVAL__HNSW_EF_SEARCH` | `retrieval.hnsw_ef_search` | `100` |
| `APE_RETRIEVAL__KEYWORD_CANDIDATE_TOP_K` | `retrieval.keyword_candidate_top_k` | `50` |
| `APE_RETRIEVAL__RRF_K` | `retrieval.rrf_k` | `60` |
| `APE_RETRIEVAL__SEMANTIC_WEIGHT` | `retrieval.semantic_weight` | `1.0` |
| `APE_RETRIEVAL__KEYWORD_WEIGHT` | `retrieval.keyword_weight` | `1.0` |
| `APE_RETRIEVAL__RERANK_ENABLED` | `retrieval.rerank_enabled` | `true` |
| `APE_RETRIEVAL__RERANK_MODE` | `retrieval.rerank_mode` | `always` |
| `APE_RETRIEVAL__RERANK_TOP_N` | `retrieval.rerank_top_n` | `20` |
| `APE_RETRIEVAL__RERANK_CANDIDATE_WINDOW` | `retrieval.rerank_candidate_window` | `25` |
| `APE_RETRIEVAL__RERANK_RETURN_N` | `retrieval.rerank_return_n` | `8` |
| `APE_RETRIEVAL__RERANK_SCORE_THRESHOLD` | `retrieval.rerank_score_threshold` | `null` |
| `APE_RETRIEVAL__RERANKER_BACKEND` | `retrieval.reranker_backend` | `noop` |
| `APE_RETRIEVAL__LANGUAGE_METADATA_SCHEMA_VERSION` | `retrieval.language_metadata_schema_version` | `2026-08-18.v1` |
| `APE_RETRIEVAL__FTS_REGCONFIG` | `retrieval.fts_regconfig` | `simple` |
| `APE_RETRIEVAL__MIN_OCR_CONFIDENCE` | `retrieval.min_ocr_confidence` | `null` |
| `APE_RETRIEVAL__MAX_CHUNKS_PER_DOCUMENT` | `retrieval.max_chunks_per_document` | `4` |
| `APE_RETRIEVAL__MAX_CHUNKS_PER_SECTION` | `retrieval.max_chunks_per_section` | `2` |
| `APE_RETRIEVAL__DEDUPLICATE_BY_CONTENT_HASH` | `retrieval.deduplicate_by_content_hash` | `true` |
| `APE_RETRIEVAL__PASSAGE_SCORING_ENABLED` | `retrieval.passage_scoring_enabled` | `false` |
| `APE_RETRIEVAL__PASSAGE_WINDOW_TOKENS` | `retrieval.passage_window_tokens` | `96` |
| `APE_RETRIEVAL__PASSAGE_OVERLAP_TOKENS` | `retrieval.passage_overlap_tokens` | `24` |
| `APE_RETRIEVAL__PASSAGE_MIN_TOKENS` | `retrieval.passage_min_tokens` | `32` |
| `APE_RETRIEVAL__MODIFIES_EXPANSION_ENABLED` | `retrieval.modifies_expansion_enabled` | `false` |
| `APE_RETRIEVAL__MODIFIES_EXPANSION_MODE` | `retrieval.modifies_expansion_mode` | `off` |
| `APE_RETRIEVAL__MAX_RELATED_SOURCES` | `retrieval.max_related_sources` | `8` |
| `APE_RETRIEVAL__MAX_RELATIONSHIP_CANDIDATES` | `retrieval.max_relationship_candidates` | `20` |
| `APE_QUERY_TRANSLATION__ENABLED` | `query_translation.enabled` | `false` |
| `APE_QUERY_TRANSLATION__BACKEND` | `query_translation.backend` | `openai` |
| `APE_QUERY_TRANSLATION__MODEL` | `query_translation.model` | `gpt-5-nano` |
| `APE_QUERY_TRANSLATION__PROMPT_VERSION` | `query_translation.prompt_version` | `retrieval-translation-v2` |
| `APE_QUERY_TRANSLATION__MIN_OUTPUT_TOKENS` | `query_translation.min_output_tokens` | `256` |
| `APE_QUERY_TRANSLATION__MAX_OUTPUT_TOKENS` | `query_translation.max_output_tokens` | `4096` |
| `APE_QUERY_TRANSLATION__REQUEST_TIMEOUT_SECONDS` | `query_translation.request_timeout_seconds` | `45` |
| `APE_QUERY_TRANSLATION__RETRY_MAX_ATTEMPTS` | `query_translation.retry_max_attempts` | `1` |
| `APE_QUERY_TRANSLATION__TARGET_LANGUAGES` | `query_translation.target_languages` | `bn,en` |
| `APE_COHERE__API_KEY` | `cohere.api_key` | `null` |
| `APE_COHERE__BASE_URL` | `cohere.base_url` | `https://api.cohere.com` |
| `APE_RERANKER__COHERE_API_KEY` | `reranker.cohere_api_key` | `null` |
| `APE_RERANKER__COHERE_BASE_URL` | `reranker.cohere_base_url` | `https://api.cohere.com` |
| `APE_RERANKER__COHERE_MODEL` | `reranker.cohere_model` | `rerank-v4.0-pro` |
| `APE_RERANKER__REQUEST_TIMEOUT_SECONDS` | `reranker.request_timeout_seconds` | `10` |
| `APE_RERANKER__PROVIDER_VERSION` | `reranker.provider_version` | `1` |
| `APE_LLM__BACKEND` | `llm.backend` | `echo` |
| `APE_LLM__MODEL` | `llm.model` | `gpt-4o-mini` |
| `APE_LLM__TEMPERATURE` | `llm.temperature` | `null` |
| `APE_LLM__MAX_TOKENS` | `llm.max_tokens` | `4096` |
| `APE_LLM__REQUEST_TIMEOUT_SECONDS` | `llm.request_timeout_seconds` | `120` |
| `APE_LLM__OLLAMA_BASE_URL` | `llm.ollama_base_url` | `http://localhost:11434` |
| `APE_LLM__OPENAI_API_KEY` | `llm.openai_api_key` | `null` |
| `APE_LLM__OPENAI_BASE_URL` | `llm.openai_base_url` | `https://api.openai.com` |
| `APE_LLM__GEMINI_API_KEY` | `llm.gemini_api_key` | `null` |
| `APE_LLM__GEMINI_BASE_URL` | `llm.gemini_base_url` | Gemini v1beta URL |
| `APE_LLM__PROVIDER_VERSION` | `llm.provider_version` | `1` |
| `APE_WEB_SEARCH__BACKEND` | `web_search.backend` | inherit |
| `APE_WEB_SEARCH__MODEL` | `web_search.model` | inherit |
| `APE_WEB_SEARCH__MAX_RESULTS` | `web_search.max_results` | `8` |
| `APE_WEB_SEARCH__MAX_EVIDENCE_CHARS` | `web_search.max_evidence_chars` | `12000` |
| `APE_WEB_SEARCH__MAX_OUTPUT_TOKENS` | `web_search.max_output_tokens` | `4096` |
| `APE_WEB_SEARCH__REQUEST_TIMEOUT_SECONDS` | `web_search.request_timeout_seconds` | `45` |
| `APE_WEB_SEARCH__OPENAI_API_KEY` | `web_search.openai_api_key` | `null` |
| `APE_WEB_SEARCH__OPENAI_BASE_URL` | `web_search.openai_base_url` | `null` |
| `APE_WEB_SEARCH__PROVIDER_VERSION` | `web_search.provider_version` | `responses-web-search-v1` |
| `APE_GENERATION__MAX_REQUEST_BYTES` | `generation.max_request_bytes` | `262144` |
| `APE_GENERATION__MAX_CONTEXT_BYTES` | `generation.max_context_bytes` | `204800` |
| `APE_GENERATION__MAX_SCHEMA_BYTES` | `generation.max_schema_bytes` | `32768` |
| `APE_GENERATION__MAX_CONTEXT_DEPTH` | `generation.max_context_depth` | `12` |
| `APE_GENERATION__MAX_CONTEXT_NODES` | `generation.max_context_nodes` | `5000` |
| `APE_GENERATION__DEFAULT_RETENTION` | `generation.default_retention` | `none` |
| `APE_GENERATION__ALLOW_FULL_RETENTION` | `generation.allow_full_retention` | `true` |
| `APE_CHAT__RESPONSE_MODE` | `chat.response_mode` | `indexed_only` |
| `APE_CHAT__RETRIEVAL_TOP_K` | `chat.retrieval_top_k` | `10` |
| `APE_CHAT__MAX_CONTEXT_CHUNKS` | `chat.max_context_chunks` | `8` |
| `APE_CHAT__CONTEXT_CHAR_BUDGET` | `chat.context_char_budget` | `12000` |
| `APE_CHAT__MAX_HISTORY_MESSAGES` | `chat.max_history_messages` | `20` |
| `APE_CHAT__SYSTEM_PROMPT_VERSION` | `chat.system_prompt_version` | `v5` |
| `APE_CHAT__INCLUDE_CITATIONS` | `chat.include_citations` | `true` |
| `APE_CHAT__CITATION_EXCERPT_MAX_CHARS` | `chat.citation_excerpt_max_chars` | `200` |
| `APE_CHAT__EVIDENCE_SCORE_MODE` | `chat.evidence_score_mode` | `whole_chunk` |
| `APE_CHAT__EVIDENCE_GATE_MODE` | `chat.evidence_gate_mode` | `enforce` |
| `APE_CHAT__MINIMUM_SEMANTIC_EVIDENCE_SCORE` | `chat.minimum_semantic_evidence_score` | `0.35` |
| `APE_CHAT__LEXICAL_CORROBORATION_FLOOR_SCORE` | `chat.lexical_corroboration_floor_score` | `0.30` |
| `APE_CHAT__LEXICAL_CORROBORATION_COVERAGE` | `chat.lexical_corroboration_coverage` | `0.50` |
| `APE_CHAT__CROSS_LANGUAGE_SEMANTIC_EVIDENCE_SCORE_THRESHOLD` | `chat.cross_language_semantic_evidence_score_threshold` | `0.30` |
| `APE_CHAT__MINIMUM_RERANKER_EVIDENCE_SCORE` | `chat.minimum_reranker_evidence_score` | `0.40` |
| `APE_CHAT__HIGH_CONFIDENCE_RERANKER_EVIDENCE_SCORE` | `chat.high_confidence_reranker_evidence_score` | `0.70` |
| `APE_CHAT__GROUNDING_MODE` | `chat.grounding_mode` | `strict` |
| `APE_CHAT__CANDIDATE_WISE_GROUNDING_ENABLED` | `chat.candidate_wise_grounding_enabled` | `false` |
| `APE_CHAT__MINIMUM_CLAIM_TOKEN_COVERAGE` | `chat.minimum_claim_token_coverage` | `0.35` |
| `APE_CHAT__MINIMUM_CLAIM_SEMANTIC_SCORE` | `chat.minimum_claim_semantic_score` | `0.25` |
| `APE_CHAT__CLAIM_SEMANTIC_REJECT_FLOOR` | `chat.claim_semantic_reject_floor` | `0.15` |
| `APE_CHAT__INSUFFICIENT_EVIDENCE_MESSAGE` | `chat.insufficient_evidence_message` | canned refusal |
| `APE_CHAT__AUTO_TITLE_MAX_CHARS` | `chat.auto_title_max_chars` | `80` |
| `APE_EVALUATION__EVALUATOR_VERSION` | `evaluation.evaluator_version` | `quality-v3` |
| `APE_EVALUATION__DEFAULT_TOP_K` | `evaluation.default_top_k` | `5` |
| `APE_EVALUATION__MAX_CASES_PER_DATASET` | `evaluation.max_cases_per_dataset` | `500` |
| `APE_EVALUATION__MINIMUM_RECALL_AT_K` | `evaluation.minimum_recall_at_k` | `0.80` |
| `APE_EVALUATION__MINIMUM_RANK_1_ACCURACY` | `evaluation.minimum_rank_1_accuracy` | `0.80` |
| `APE_EVALUATION__MINIMUM_CROSS_LINGUAL_RECALL_AT_K` | `evaluation.minimum_cross_lingual_recall_at_k` | `0.75` |
| `APE_EVALUATION__MINIMUM_FILTERED_CORRECTNESS` | `evaluation.minimum_filtered_correctness` | `0.95` |
| `APE_EVALUATION__MAXIMUM_FALSE_REFUSAL_RATE` | `evaluation.maximum_false_refusal_rate` | `0.10` |
| `APE_EVALUATION__MAXIMUM_FALSE_ACCEPT_RATE` | `evaluation.maximum_false_accept_rate` | `0.0` |
| `APE_EVALUATION__MAXIMUM_ACCEPTED_WITHOUT_RELEVANT_EVIDENCE_RATE` | `evaluation.maximum_accepted_without_relevant_evidence_rate` | `0.0` |
| `APE_EVALUATION__MINIMUM_GROUNDEDNESS` | `evaluation.minimum_groundedness` | `0.80` |
| `APE_EVALUATION__MINIMUM_CITATION_COVERAGE` | `evaluation.minimum_citation_coverage` | `0.80` |
| `APE_EVALUATION__MAXIMUM_P95_LATENCY_MS` | `evaluation.maximum_p95_latency_ms` | `750` |
| `APE_EVALUATION__MAXIMUM_METRIC_REGRESSION` | `evaluation.maximum_metric_regression` | `0.02` |
| `APE_EVALUATION__MINIMUM_RERANKER_NDCG_GAIN` | `evaluation.minimum_reranker_ndcg_gain` | `0.02` |
| `APE_EVALUATION__MAXIMUM_RERANKER_LATENCY_PENALTY_MS` | `evaluation.maximum_reranker_latency_penalty_ms` | `150` |
| `APE_EVALUATION__RERANKER_CANDIDATES` | `evaluation.reranker_candidates` | `lexical,embedding,embedding_max` |
| `APE_AI_POLICY__REQUEST_OVERRIDE_MODE` | `ai_policy.request_override_mode` | `compatibility` |
| `APE_AI_POLICY__MAX_REQUEST_TOP_K` | `ai_policy.max_request_top_k` | `100` |
| `APE_AI_POLICY__SOURCE_POLICY_MODE` | `ai_policy.source_policy_mode` | `off` |
| `APE_AI_POLICY__SOURCE_POLICY_DEPLOYMENT_CAP` | `ai_policy.source_policy_deployment_cap` | `enforce` |
| `APE_AI_POLICY__ENABLED_RETRIEVAL_STRATEGIES` | `ai_policy.enabled_retrieval_strategies` | `semantic,hybrid` |
| `APE_AUTH__ENABLED` | `auth.enabled` | `false` |
| `APE_AUTH__KEY_PEPPER` | `auth.key_pepper` | `null` |
| `APE_AUTH__VERIFY_CACHE_ENABLED` | `auth.verify_cache_enabled` | `true` |
| `APE_AUTH__VERIFY_CACHE_TTL_SECONDS` | `auth.verify_cache_ttl_seconds` | `60` |
| `APE_AUTH__VERIFY_CACHE_BACKEND` | `auth.verify_cache_backend` | `redis` |
| `APE_AUTH__RATE_LIMIT_ENABLED` | `auth.rate_limit_enabled` | `true` |
| `APE_AUTH__RATE_LIMIT_REQUESTS` | `auth.rate_limit_requests` | `1000` |
| `APE_AUTH__RATE_LIMIT_WINDOW_SECONDS` | `auth.rate_limit_window_seconds` | `60` |
| `APE_AUTH__RATE_LIMIT_FAIL_OPEN` | `auth.rate_limit_fail_open` | `false` |
| `APE_AUTH__ADMIN_LOGIN_RATE_LIMIT_REQUESTS` | `auth.admin_login_rate_limit_requests` | `5` |
| `APE_AUTH__ADMIN_LOGIN_RATE_LIMIT_WINDOW_SECONDS` | `auth.admin_login_rate_limit_window_seconds` | `60` |
| `APE_AUTH__ADMIN_JWT_SECRET` | `auth.admin_jwt_secret` | `null` |
| `APE_AUTH__ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES` | `auth.admin_access_token_expire_minutes` | `15` |
| `APE_AUTH__ADMIN_REFRESH_TOKEN_EXPIRE_DAYS` | `auth.admin_refresh_token_expire_days` | `14` |
| `APE_AUTH__ADMIN_COOKIE_SECURE` | `auth.admin_cookie_secure` | `false` |
| `APE_AUTH__ADMIN_COOKIE_SAMESITE` | `auth.admin_cookie_samesite` | `lax` |
| `APE_AUTH__ADMIN_COOKIE_DOMAIN` | `auth.admin_cookie_domain` | `null` |

---

## Insight: how to live with this many keys

The list looks large because three jobs share one Settings object: **run the
process**, **build an index**, **answer a question**. They should not all be
tuned at once.

### Essential (must be correct or the deploy is wrong)

Treat these as the deployment contract, not knobs:

| Area | Keys |
| ---- | ---- |
| Environment | `APE_APP__ENV`, `APE_RUNTIME__PROFILE` |
| Secrets / auth | DB/Redis/MinIO/webhook secrets, `APE_AUTH__ENABLED`, pepper, JWT |
| Providers | `APE_LLM__BACKEND/MODEL/OPENAI_API_KEY`, `APE_EMBEDDING__BACKEND/MODEL/DIMENSIONS`, `APE_COHERE__API_KEY` |
| Index identity | `APE_RETRIEVAL__EMBEDDING_SET_VERSION` (bump on provider/model change) |
| Production path | `APE_STORAGE__BACKEND=minio`, `APE_JOBS__BACKEND=taskiq`, `APE_RETRIEVAL__STRATEGY=hybrid`, `APE_RETRIEVAL__RERANK_ENABLED=true`, `APE_MALWARE_SCAN__BACKEND=clamav` |
| Product safety | `APE_CHAT__EVIDENCE_GATE_MODE`, `APE_CHAT__SYSTEM_PROMPT_VERSION` |

Without these, you either cannot boot in production or you are testing pipeline
mechanics (`hash`/`echo`) and mistaking that for RAG quality.

### Tune (change with a hypothesis, one at a time)

| Stage | Keys worth turning |
| ----- | ------------------ |
| Ingestion | OCR on/off + lang, parse quality floors, `chunking.target/max/min_tokens`, `structure_score_threshold` |
| Recall | candidate `top_k`s, `hnsw_ef_search`, RRF weights, `query_translation.enabled`, `rerank_mode` |
| Precision / cost | `reranker_backend`, `rerank_return_n`, diversity caps, `max_context_chunks`, `context_char_budget` |
| Grounding | semantic 0.35, reranker 0.40/0.70, lexical 0.30/0.50, `grounding_mode`, `candidate_wise_grounding_enabled` |
| Policy | `source_policy_mode`, `modifies_expansion_mode`, `response_mode` |
| Ops | job lease (slow OCR), pool sizes, auth cache TTL, webhook retries |
| Eval bars | `APE_EVALUATION__*` — accept criteria only |

Rule: if the gold chunk is **absent** from retrieval diagnostics, tune
ingestion/recall. If it is present but the model refuses or wanders, tune
grounding/context. Temperature is last.

### Redundant, overlapping, or leave-alone

These exist for compatibility, provenance, or unused seams. Do not “tune” them
expecting product change:

| Item | Why it feels like a duplicate |
| ---- | ----------------------------- |
| `APE_RETRIEVAL__RERANK_ENABLED` vs `RERANK_MODE` | Boolean is legacy. Mode is the control. |
| `APE_RETRIEVAL__MODIFIES_EXPANSION_ENABLED` vs `MODE` | Mode wins whenever it is not `off`. Root `.env.example` sets `MODE=expand` with `ENABLED=false` — expansion **is on**. |
| `APE_CHAT__RETRIEVAL_TOP_K` vs `APE_RETRIEVAL__DEFAULT_TOP_K` | Project `top_k` writes both. Prefer retrieval `default_top_k` at deployment. |
| `APE_RETRIEVAL__RERANK_TOP_N` vs `RERANK_CANDIDATE_WINDOW` | Window = `max` of both. Keep one mental number (25) unless you have a measured reason. |
| `APE_RERANKER__COHERE_API_KEY` | Use `APE_COHERE__API_KEY`. |
| Extra OpenAI keys (`EMBEDDING` / `WEB_SEARCH` / `LLM`) | Needed only when those backends differ. On `hosted_managed`, fill `APE_LLM__OPENAI_API_KEY` + `APE_COHERE__API_KEY`. |
| `APE_CHUNKING__OVERLAP_TOKENS` | Not applied by current splitters. |
| `recursive_character` | Same code path as `recursive_fallback`. |
| `*_PROVIDER_VERSION`, `chunker_version`, `token_count_method`, `language_metadata_schema_version`, translation/web `prompt/provider_version` | Pins for snapshots/evals. Change when the algorithm changes, not to “improve search”. |
| `APE_RETRIEVAL__AUTO_INDEX` | Same full-build path as auto_embed in current lifecycle. |
| `APE_RETRIEVAL__SCORE_THRESHOLD` vs chat evidence scores | SQL cosine cut vs **admission** bars. Leaving SQL threshold `null` is correct; the gate is chat. |
| `APE_APP__NAME` / `VERSION` | Identity. |
| Eval latency `750ms` on hosted Cohere+translate | Often fails as an **accept bar**, not as a runtime limit. Tune only if you use eval gates for go/no-go. |

### Two traps in the example env files

1. **Code default ≠ Docker example** for `grounding_mode` (strict vs balanced),
   `candidate_wise_grounding_enabled` (false vs true), `source_policy_mode`
   (off vs enforce), `modifies_expansion_mode` (off vs expand), OCR, embeddings.
   If you reason from code defaults while running Compose, you will mis-debug.
2. **Jobs and conversations snapshot config.** Editing `.env` and restarting
   does not rewrite an open conversation or a running index job. Refresh the
   conversation / enqueue a new build.

### What I would simplify later (not done here)

If the goal is a smaller control panel: collapse legacy booleans into modes;
keep a single `top_k`; keep a single rerank window; stop exposing unused
`overlap_tokens`; keep provenance versions internal. The RAG surface that
actually matters is: **chunk sizes, embedding identity, hybrid candidate depth,
rerank mode, evidence bars, response mode, source policy.**
