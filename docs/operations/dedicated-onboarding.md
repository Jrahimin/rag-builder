# Dedicated Hosted Pilot Onboarding

Complete and approve this record per deployment. Commercial policy is input, not code.

## Approved configuration record

| Decision | Supported choices |
| --- | --- |
| Runtime profile | `hosted_openai` or `private_ollama` |
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
| LLM | OpenAI route | Ollama route | other adapters |
| Embeddings | OpenAI route | Ollama route | hash/fake in production, other adapters |
| Reranker | measured active configuration | same | toggle without evaluation |
| OCR | Paddle when explicitly enabled | Paddle when explicitly enabled | noop when enabled |
| Malware | ClamAV | ClamAV | disabled production scanning |

## Supported file matrix

PDF, DOCX, UTF-8 TXT, Markdown, PNG, JPEG, TIFF, and WebP pass extension/MIME/signature
validation. Password-protected/corrupt files fail before expensive processing. Images
require real OCR. Stock Paddle does not support Bangla OCR; Unicode Bengali text layers
and UTF-8 text documents remain supported.

## Responsibility boundary

| Operator owns | Customer/host application owns |
| --- | --- |
| deployment, images, migrations, backup/restore, dependency health, provider configuration, incident diagnostics | users/sessions/RBAC, business authorization, receiver availability/deduplication, source-file rights, product UX, usage policy |

No licensing, SLO, pricing, quota, retention, support tier, region, or cost-rate commitment
is created here. Record approved values in the customer operating agreement.

