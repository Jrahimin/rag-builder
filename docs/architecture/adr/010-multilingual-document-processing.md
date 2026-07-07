# ADR-010: Multilingual Document Processing

## Status

Accepted — 2026-07-07

## Context

Phase 1 chunking and retrieval used Latin-only tokenization (`[a-z0-9]+`) and PostgreSQL FTS `english` regconfig. Bangla and other non-Latin scripts produced zero BM25 tokens, inconsistent chunk sizing, and poor keyword retrieval. Scanned PDFs and image uploads had no OCR path.

## Decision

1. **Unicode-property tokenization** — use the `regex` package with `\p{Letter}`, `\p{Number}`, `\p{Mark}`; expose `token_count_method=unicode_property_v1`. Do not use stdlib `\w`.
2. **Shared normalization** — `platform/domain/text_normalizer.py` is the single path for ingestion and query (`normalize_for_storage`, `normalize_for_indexing`, `normalize_for_query`).
3. **Generic OCR** — `OCRProvider` contract with PaddleOCR as optional implementation (`requirements/ocr.txt`); disabled by default.
4. **Language metadata** — heuristic script-ratio detection with `language`, `language_confidence`, `languages`, `is_mixed` on parsed documents and `documents.language_confidence`.
5. **Configurable FTS** — `RetrievalConfig.fts_regconfig` defaults to `simple`.
6. **Reindex CLI** — `app/cli/reindex_cli.py` for post-tokenizer migrations.
7. **OCR confidence** — per-page metadata in `structure_hints`; optional `min_ocr_confidence` retrieval filter.
8. **Per-document OCR language** — `documents.ocr_lang` optional on upload/reprocess; deployment default `APE_OCR__LANG=en`; language-keyed OCR provider pool in `ocr_factory`.

## Consequences

### Positive

- Bangla-first support without Bangla-specific tokenizers or chunk strategies
- Query/index symmetry for BM25 and lexical reranking
- Optional OCR without bloating base dependencies
- Mixed-language corpora handled via semantic chunking preference and OpenAI embeddings

### Negative

- Base dependency on `regex` package
- FTS `simple` still language-agnostic — semantic path carries mixed-language quality
- Existing indexes require reindex after tokenizer upgrade
- **Bangla OCR is not production-ready in Phase 1.** Unicode Bengali in digital documents works; scanned or custom-font Bangla PDFs cannot be recovered reliably. PaddleOCR 3.7 has no stock `bn` models (`ocr_lang=bn` fails); default English OCR on Bangla script produces wrong output. Requires a future Bengali-capable `OCRProvider` — see [multilingual_support.md](../../features/multilingual_support.md#known-limitation-bangla-bengali-ocr).

## Alternatives considered

| Alternative | Rejected because |
|-------------|------------------|
| stdlib `\w` tokenization | Engine-dependent Unicode behavior |
| Per-language PostgreSQL dictionaries | Operational complexity; BM25 on Unicode tokens is sufficient for Phase 1 |
| Bangla-specific chunk strategy | Violates Unicode-first design |
| OCR microservice | Over-engineering for self-hosted Phase 1 |
