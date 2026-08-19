# ADR-017: Google Vision as the opt-in Bangla OCR backend

## Status

Accepted — 2026-08-10

## Context

ADR-010 introduced a provider-neutral OCR contract and PaddleOCR fallback. PaddleOCR 3.7 has no
stock Bengali model, so scans and PDFs with Bijoy/custom-font encodings cannot be recovered by that
backend. Unicode Bangla PDFs can be recognized from their text layer, while scans and custom-font
documents expose no reliable Bengali script before OCR.

OCR providers receive document pixels and may run outside the deployment. Language routing and data
residency must therefore remain explicit deployment choices.

## Decision

1. Add Google Cloud Vision `DOCUMENT_TEXT_DETECTION` behind the existing `OCRProvider` contract,
   using the REST API through the base `httpx` dependency.
2. Keep `OcrConfig.backend` for the existing general fallback and add the blunt
   `bangla_backend` setting for Bengali. Do not introduce a general language/provider registry.
3. Resolve language once per PDF: explicit `ocr_lang`, then Bengali Unicode ratio, then primary
   detected language, then the deployment default. Empty/image-only samples use the default.
4. When the resolved language is `bn` and the Bangla backend is enabled, OCR every page and use
   Vision as the sole extraction source. PyMuPDF still supplies page count and a detection sample;
   native candidates and PDFium are skipped.
5. Do not send Vision `languageHints` until benchmarks show a quality benefit for mixed Bangla and
   English documents.
6. Do not add Bijoy/font heuristics, encoding penalties, or preliminary OCR for language detection.
7. Bound outbound work with `max_ocr_pages_per_document`. Retry transient Vision errors with bounded
   exponential backoff and propagate exhausted retryable errors to the durable job runtime.
8. Keep the API key in live deployment configuration only. Durable configuration snapshots and the
   operator read model never serialize it.
9. Startup preflight constructs configured OCR providers and validates credential presence without
   sending document data or making a Vision recognition request.

## Consequences

### Positive

- Bangla PDFs, scans, and images have a production-capable OCR path without changing module-facing
  contracts or the parse-quality scorer.
- Paddle and Vision can coexist in the same worker through an effective-backend provider pool.
- English parsing and page-level native/PDFium/Paddle competition remain unchanged.
- The page cap and durable retry behavior bound cost and prevent transient failures from committing
  corrupt partial output.

### Negative

- Rasterized page/image data leaves the deployment for Google when the backend is enabled.
- Every page on the Bangla route incurs network latency and provider cost.
- Bangla scans, images, and Bijoy/custom-font PDFs still require explicit `ocr_lang=bn` or a
  Bangla-oriented deployment default because there is no trustworthy pre-OCR script sample.
- Vision batching and `languageHints` remain deferred pending benchmarks.

## Alternatives considered

| Alternative | Rejected because |
| ----------- | ---------------- |
| Extend PaddleOCR with an unverified custom Bengali model | No maintained stock model or benchmarked artifact is available in the current deployment contract |
| Run preliminary OCR to detect language | Doubles work and cost, and makes routing depend on the backend it is trying to select |
| Add Bijoy/font/encoding heuristics | Fragile document-specific behavior would conflict with the Unicode-first architecture |
| General language-to-provider registry | Premature abstraction for one additional routed language |
| Per-page native-versus-Vision competition | Risks accepting custom-font glyph soup and violates the sole-source requirement for the Bangla route |

## Amendment: Vision layout contract (2026-08-17)

The Vision adapter preserves `fullTextAnnotation.pages` as provider-neutral normalized geometry.
The PDF workflow carries the selected candidate's elements instead of flattening and re-splitting
page text. Conservative aligned-column runs may become typed tables; the inference contains no
Bangla terms and providers without layout retain the text-only fallback. Existing documents require
reprocessing to receive workflow version 2.0.0 structure.
