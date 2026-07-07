# ADR-011: Parser Quality Scoring and PDF Engine Fallback

## Status

Accepted

## Context

Some PDFs — especially legacy government documents with custom fonts and broken `/ToUnicode` maps — extract as garbled control characters or private-use glyphs. Indexing that text collapses retrieval quality. This is a parsing problem, not an embedding or retrieval problem.

PyMuPDF alone cannot recover all digital PDFs. OCR is expensive and sometimes worse than a partial native extraction. Production systems need Unicode-first quality assessment, page-level fallback, and observable parser decisions.

## Decision

1. **Page-level quality first.** Score every page with a generic Unicode-property scorer in `platform/domain/parse_quality.py`. Derive document summaries from page outcomes.
2. **Lazy parser fallback.** Default order: PyMuPDF → PDFium (`pypdfium2`). Only degraded pages trigger the next parser.
3. **OCR last.** OCR runs only for pages still below threshold when `APE_OCR__ENABLED=true`. OCR output is a candidate, not an automatic replacement.
4. **Highest-quality wins.** For each page, keep the candidate with the highest `parse_quality_score`. Parser order is a tie-breaker only.
5. **Partial success.** Index accepted pages; mark failed pages in parsed JSON sidecar metadata. Fail the document only when accepted pages fall below `APE_PARSING__MIN_DOCUMENT_SUCCESS_RATIO` or no usable text exists.
6. **Separate metrics.** `parser_confidence` (parser-reported confidence) and `parse_quality_score` (domain scorer) remain distinct fields.
7. **Metadata.** Persist `accepted_parser`, `parse_quality_score`, and `extraction_method` on `documents`. Store parser attempts, page quality, timings, warnings, and OCR provenance in `parsed/v{n}.json`.

## Configuration

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `APE_PARSING__PDF_TEXT_PARSERS` | `pymupdf,pdfium` | Validated parser order |
| `APE_PARSING__MIN_PAGE_QUALITY_SCORE` | `0.55` | Accept page threshold |
| `APE_PARSING__MIN_DOCUMENT_SUCCESS_RATIO` | `0.2` | Minimum accepted-page ratio |
| `APE_PARSING__MIN_TEXT_CHARS` | `20` | Near-empty extraction guard |

## Consequences

- Normal PDFs stay on the fast PyMuPDF path.
- Broken text-layer PDFs get a second chance via PDFium without OCR cost.
- OCR cost is limited to genuinely degraded pages.
- Parser analytics are available through structured logs and parsed sidecar metadata.
- `pypdfium2` becomes a base runtime dependency for PDF ingestion.
- **OCR fallback does not guarantee correct script.** For Bangla government PDFs with broken font maps, the quality gate correctly rejects native text and triggers OCR, but PaddleOCR 3.7 cannot read Bengali (`bn` unsupported; English model misrecognizes script). Document may reach `ready` with poor Bangla content until a Bengali-capable OCR backend is added — see [multilingual_support.md](../../features/multilingual_support.md#known-limitation-bangla-bengali-ocr).

## Alternatives considered

- **OCR-first for all PDFs** — rejected: too expensive for the common case.
- **Always run both parsers** — rejected: doubles parse cost on healthy PDFs.
- **Language-specific parser forks** — rejected: conflicts with Unicode-first design.
