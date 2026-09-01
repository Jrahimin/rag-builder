# Multilingual Document Processing

APE treats multilingual corpora as first-class through Unicode-property tokenization, shared text normalization, optional OCR, and language confidence metadata.

## Capabilities

| Area | Behavior |
|------|----------|
| Tokenization | `regex` package with `\p{Letter}`, `\p{Number}`, `\p{Mark}` (`unicode_property_v1`) |
| Normalization | Shared `text_normalizer` for parsers, chunking, BM25, and query paths |
| Languages | Heuristic script-ratio detection with confidence and mixed-language support |
| OCR | Optional `OCRProvider`: PaddleOCR for the existing pipeline; Google Cloud Vision for Bangla |
| PDF parsing | Page-level Unicode quality scoring with PyMuPDF → PDFium → OCR fallback |
| FTS | Configurable `APE_RETRIEVAL__FTS_REGCONFIG` (default `simple`) |
| Dense retrieval | `hosted_managed`: Cohere `embed-v4.0` @ 1024 (`QUERY`/`DOCUMENT`); `hosted_openai`: `text-embedding-3-large` @ 1024 |
| Query translation | Optional, query-only, one target language, default `gpt-5-nano` and `retrieval-translation-v2`; never cited |
| Evidence gate | Applied multilingual reranker relevance is primary; original cosine is fallback. `hosted_managed` examples enforce with current defaults. |
| Reindex | `python -m app.cli.reindex_cli` after tokenizer upgrades |

## Configuration

```env
APE_CHUNKING__TOKEN_COUNT_METHOD=unicode_property_v1
APE_OCR__ENABLED=false
APE_OCR__BACKEND=noop
APE_OCR__BANGLA_BACKEND=noop
APE_OCR__LANG=en
APE_OCR__GOOGLE_API_KEY=
APE_OCR__BANGLA_MIN_RATIO=0.10
APE_OCR__MAX_OCR_PAGES_PER_DOCUMENT=100
APE_RETRIEVAL__FTS_REGCONFIG=simple
APE_RETRIEVAL__MIN_OCR_CONFIDENCE=
APE_RETRIEVAL__FILTERABLE_METADATA_KEYS=source,tags,ocr_confidence
APE_EMBEDDING__MODEL=embed-v4.0
APE_EMBEDDING__DIMENSIONS=1024
APE_RETRIEVAL__EMBEDDING_SET_VERSION=3
APE_QUERY_TRANSLATION__ENABLED=true
APE_QUERY_TRANSLATION__MODEL=gpt-5-nano
APE_QUERY_TRANSLATION__PROMPT_VERSION=retrieval-translation-v2
APE_QUERY_TRANSLATION__MAX_OUTPUT_TOKENS=4096
APE_RETRIEVAL__RERANKER_BACKEND=cohere
APE_RETRIEVAL__RERANK_MODE=always
APE_COHERE__API_KEY=
APE_COHERE__BASE_URL=https://api.cohere.com
APE_CHAT__MINIMUM_SEMANTIC_EVIDENCE_SCORE=0.35
APE_CHAT__EVIDENCE_GATE_MODE=enforce
APE_CHAT__MINIMUM_RERANKER_EVIDENCE_SCORE=0.40
APE_CHAT__LEXICAL_CORROBORATION_FLOOR_SCORE=0.30
APE_CHAT__LEXICAL_CORROBORATION_COVERAGE=0.50
APE_PARSING__PDF_TEXT_PARSERS=pymupdf,pdfium
APE_PARSING__MIN_PAGE_QUALITY_SCORE=0.55
APE_PARSING__MIN_DOCUMENT_SUCCESS_RATIO=0.2
```

PaddleOCR requires its optional dependency set:

```bash
pip install -r backend/requirements/ocr.txt
```

Set `APE_OCR__ENABLED=true` and `APE_OCR__BACKEND=paddle` for the existing
English/general fallback. Google Vision uses the base `httpx` dependency; enable the Bangla route
with `APE_OCR__BANGLA_BACKEND=google_vision` and `APE_OCR__GOOGLE_API_KEY`.

### Per-document OCR language

| Source | Precedence |
|--------|------------|
| `ocr_lang` on upload (form field) or reprocess (query) | Highest — stored on `documents.ocr_lang` |
| Bengali script ratio in a usable PDF text layer | Second — `bn` when it meets `APE_OCR__BANGLA_MIN_RATIO` |
| Primary detected script | Third — used when the sample has letters and is not Bangla |
| `APE_OCR__LANG` | Deployment default for scans, images, and other letterless samples |

Use an explicit language when the source has no reliable Unicode text layer. Aliases are normalized
at ingest (`eng`→`en`, `bangla`/`bengali`/`ben`→`bn`). Mixed Unicode Bangla and English routes to
Bangla when the Bengali ratio reaches the configured threshold.

The worker keeps a small in-process OCR provider pool keyed by effective backend, language, and GPU
flag, so Paddle and Vision can coexist without reinitialization on every document.

<a id="known-limitation-bangla-bengali-ocr"></a>

## Bangla (Bengali) OCR routing and limitations

When the resolved language is `bn` and the Bangla backend is enabled, Google Vision
`DOCUMENT_TEXT_DETECTION` OCRs every PDF page or the whole uploaded image. Native/PDFium text is
not registered as a candidate on this route; Vision is the sole extraction source. Requests omit
`languageHints` so mixed Bangla and English content is detected by Vision.

| Document type | Auto-detectable? | How it reaches Google Vision |
| ------------- | ---------------- | ---------------------------- |
| Unicode Bangla PDF | Yes — Bengali script in the text layer | Detection or explicit `ocr_lang=bn` / default `APE_OCR__LANG=bn` |
| Mixed Unicode Bangla + English PDF | Yes — meaningful Bengali presence routes as Bangla | Detection or explicit/default `bn` |
| Bijoy / custom-font Bangla PDF | **No** — extracts as Latin codepoints | Explicit `ocr_lang=bn` or deployment default `bn` required |
| Bangla scan / image with no usable text layer | **No** — nothing exists to inspect before OCR | Explicit `ocr_lang=bn` or deployment default `bn` required |

APE deliberately does not add Bijoy/font heuristics, encoding penalties, or a preliminary OCR pass
only to detect language. A Bangla-oriented deployment can set `APE_OCR__LANG=bn`; mixed deployments
should send `ocr_lang=bn` for scans, images, and custom-font documents.

Google receives rasterized page/image content. Enabling the backend is therefore an explicit
deployment data-residency decision. `APE_OCR__MAX_OCR_PAGES_PER_DOCUMENT` bounds request volume and
fails oversized OCR targets rather than silently truncating them.

Vision block/paragraph/word geometry is normalized into provider-neutral coordinates and preserved
through the PDF parser. Consecutive rows with conservative aligned-column evidence become typed
table elements; ambiguous layout remains ordinary paragraphs. This behavior is script-neutral and
does not contain Bangla vocabulary rules. Reprocessing is required to obtain these elements for
documents parsed by workflow version 1.x.

### PDF mixed-content handling

| Setting | Default | Effect |
|---------|---------|--------|
| `APE_OCR__MIN_TEXT_CHARS` | `20` | Discard short OCR noise (e.g. logo misreads) |
| `APE_OCR__MIN_PAGE_CONFIDENCE` | `0.3` | Discard low-confidence OCR |
| `APE_OCR__MIN_IMAGE_AREA_RATIO` | `0.08` | Per-image OCR only for images ≥ 8% of page area |

For non-Bangla documents, pages with **both** native text and embedded images retain the existing
behavior: score native text first, try PDFium only for degraded pages, and invoke the configured
general OCR backend only when still below threshold. OCR output is kept only when it beats the best
candidate. The Bangla route instead OCRs every page and does not compete with native candidates.

## Query-only multilingual retrieval

Hybrid search always runs original dense and original lexical branches with no language filter.
When translation is enabled and the active immutable build has language inventory, at most one
target-language rewrite is added (`gpt-5-nano` by default). Translation runs only when it can
materially improve retrieval: Bangla → English, or a bounded Banglish/code-switched rewrite to
English. Ordinary Latin-script queries do not auto-translate to Bangla merely because Bangla
exists in the corpus. Mixed-script queries keep both scripts in the original branches and skip
the rewrite. Hard-scoped retrieval uses per-document language counts from the index-build
manifest so a same-language document does not spend a translation call. Translated branches
include `target OR mixed OR unknown` rows. Original chunks remain the only evidence and
citations. The default minimum translation output budget is 256 tokens, still capped at 2048
and overridable through `APE_QUERY_TRANSLATION__MIN_OUTPUT_TOKENS` /
`APE_QUERY_TRANSLATION__MAX_OUTPUT_TOKENS`. `hosted_managed` enables translation and Cohere
rerank by default; local and pytest stacks keep them off. Cut over an existing OpenAI embedding
set with rebuild → validate → activate. See [ADR-018](../architecture/adr/018-multilingual-retrieval-v1.md).

## Reindex after upgrades

Embedding dimension changes require the Alembic migration before startup. Migration `0026`
invalidates existing index builds and pointers because vectors of different dimensions cannot share
one pgvector column. After migration, create and activate a complete build through **Rebuild index**
in Lifecycle (`POST .../index-builds/reembed`), then Activate. Documents return
to `chunked` during the migration and become `ready` when that snapshot is
activated.

Parser 2.0.0 / chunker 3.0.0 also require **document reprocess** (not only Rebuild index) so OCR
pages keep layout elements and table chunks. After activation, create a new conversation or refresh
the conversation snapshot; old snapshots keep the previous evidence mode and thresholds.

```bash
python -m app.cli.reindex_cli document --project-id <uuid> --document-id <uuid>
python -m app.cli.reindex_cli project --project-id <uuid> --full
python -m app.cli.reindex_cli project --project-id <uuid> --dry-run
```

## Acceptance scenarios

- Unicode Bengali text (valid text layer or `.txt`/`.docx`) + Bangla query → non-zero tokens; hybrid retrieval returns relevant chunks
- English query + Bengali evidence and Bengali query + English evidence → dense recall; optional query translation is an additive hybrid branch, not a citation source
- Same-script cross-language pairs (for example English/French) behave like different-script pairs
- Code-switched queries are evaluated without runtime language or script routing
- Topically near but wrong cross-language evidence remains below the false-accept gate
- English-only and mixed-language documents behave symmetrically
- Low OCR confidence chunks filterable via `APE_RETRIEVAL__MIN_OCR_CONFIDENCE`
- Ellipsis-terminated OCR lines split on sentence boundaries
- Unicode or mixed Bangla PDF + configured Vision backend → auto-detected and OCR-first
- Bangla scan / Bijoy PDF + explicit or deployment-default `bn` → Vision OCR-first
- OCR tables retain page provenance; oversized tables repeat captions and headers across row groups
- Tiny OCR fragments merge into adjacent chunks instead of occupying retrieval slots
- English corpus → existing PyMuPDF → PDFium → configured general OCR behavior unchanged

See ADR-010, ADR-011, ADR-017, ADR-018, and `docs/learning/multilingual-text-processing.md`.
