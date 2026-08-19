# Dedicated Hosted Pilot Onboarding

Complete and approve this record per deployment. Commercial policy is input, not code.

## Approved configuration record

| Decision | Supported choices |
| --- | --- |
| Runtime profile | `hosted_managed` (preferred), `hosted_openai` (deprecated compatibility), or `private_ollama` |
| Isolation | one operator-managed deployment per customer; Projects remain data boundaries |
| Auth | organization M2M API keys; admin credential is operator-only |
| Storage | PostgreSQL/pgvector + Redis + MinIO in the supported profile |
| TLS/DNS | customer-specific hostname and operator-managed certificate boundary |
| Backup destination/retention | explicitly approved per customer |
| Region/data residency | explicitly approved per customer |
| Provider models/dimensions | exact model, dimension, base URL, provider version |
| Usage-cost visibility | token counts exist; currency/rates/reporting require approval |

## Supported provider matrix

| Capability | Certified hosted | Certified private | Not certified |
| --- | --- | --- | --- |
| LLM | OpenAI `gpt-5.6-luna` | Ollama route | other adapters |
| Embeddings | Cohere `embed-v4.0` (`hosted_managed`); OpenAI route (`hosted_openai`) | Ollama route | hash/fake in production, other adapters |
| Reranker | Cohere `rerank-v4.0-pro` with Always default; degrade to RRF+cosine | same degrade path | toggle without evaluation |
| OCR | Google Vision when enabled (`hosted_managed`) | Paddle when explicitly enabled | noop when enabled |
| Malware | ClamAV | ClamAV | disabled production scanning |

## Supported file matrix

PDF, DOCX, UTF-8 TXT, Markdown, PNG, JPEG, TIFF, and WebP pass extension/MIME/signature
validation. Password-protected/corrupt files fail before expensive processing. Images
require real OCR. Stock Paddle does not support Bangla OCR; Unicode Bengali text layers
and UTF-8 text documents remain supported.

## Embedding cutover workflow

Configuration → rebuild → validate → activate → retrieve/diagnose → rollback.

Live `APE_EMBEDDING__*` is the **next build target**. Search, chat grounding, and
message `embedding_set_version` follow the **active** build identity. Keep the
previous provider key until the retained rollback target is retired. Translation
and rerank remain independently overridable per Project; they degrade on provider
failure. Embedding incompatibility does not degrade.

Evidence and claim-grounding thresholds are calibration for the active
embed/rerank pair. Hosted examples use `APE_CHAT__EVIDENCE_GATE_MODE=enforce`
with semantic `0.35`, applied reranker `0.40`, lexical `0.30`/`0.50`, and claim
`0.35`/`0.25`/`0.15`. Recalibrate in Test Lab if a corpus disagrees; use
`observe` only while measuring. Rollback refuses if the retained build's
embedding credentials are no longer available.

## Responsibility boundary

| Operator owns | Customer/host application owns |
| --- | --- |
| deployment, images, migrations, backup/restore, dependency health, provider configuration, incident diagnostics | users/sessions/RBAC, business authorization, receiver availability/deduplication, source-file rights, product UX, usage policy |

No licensing, SLO, pricing, quota, retention, support tier, region, or cost-rate commitment
is created here. Record approved values in the customer operating agreement.

