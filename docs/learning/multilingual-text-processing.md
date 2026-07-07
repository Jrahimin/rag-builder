# Multilingual Text Processing

How APE handles non-Latin scripts, mixed-language corpora, and OCR-derived text.

## Why this exists

Latin-only tokenization breaks Bangla, Arabic, Devanagari, and CJK text for BM25 and chunk sizing. Production RAG needs one normalization and tokenization path across scripts.

## Tokenization design

APE uses the third-party `regex` package (not stdlib `re` with `\w`):

```python
_TOKEN_PATTERN = regex.compile(r"[\p{Letter}\p{Number}\p{Mark}]+", regex.UNICODE)
```

- **Letters** — all Unicode scripts
- **Numbers** — numeric characters across scripts
- **Marks** — combining marks that belong to words

Lowercasing applies only to tokens containing Latin letters. Other scripts keep original casing.

Method id: `unicode_property_v1` (`APE_CHUNKING__TOKEN_COUNT_METHOD`).

## Normalization

`platform/domain/text_normalizer.py` provides:

| Function | Use |
|----------|-----|
| `normalize_for_storage` | Parser output cleanup |
| `normalize_for_indexing` | BM25 / FTS input |
| `normalize_for_query` | Search queries (same rules as indexing) |

Steps: NFC, OCR line-break cleanup, punctuation unification, mixed-script spacing, whitespace collapse.

## Language detection

`platform/domain/language_detection.py` uses script block ratios:

- Outputs `primary_language`, `confidence`, `languages`, `is_mixed`
- Persisted on `documents.language` and `documents.language_confidence`

## OCR path

When `APE_OCR__ENABLED=true`:

1. Image uploads route to `ImageOcrParserProvider`
2. PDF image-only or sparse pages use PaddleOCR via `PyMuPDFParserProvider`
3. Per-page confidence stored in `structure_hints.ocr_pages`
4. **Language**: `APE_OCR__LANG` defaults to `en`; optional `documents.ocr_lang` from upload/reprocess overrides per document
5. **Provider pool**: `ocr_factory.get_ocr_provider(lang=...)` caches Paddle instances per language (bounded pool per worker process)

Install: `pip install -r backend/requirements/ocr.txt`

### Bangla OCR limitation (Phase 1)

Unicode tokenization and chunking work for Bengali **when the parsed text contains real `\p{Bengali}` characters**. Scanned or custom-font Bangla PDFs are a separate problem: PaddleOCR 3.7 has no stock `bn` model, and English OCR misreads Bangla script. See [multilingual_support.md](../features/multilingual_support.md#known-limitation-bangla-bengali-ocr).

## Mixed-language corpora

- Chunk strategy selector prefers `SEMANTIC` for low OCR quality, mixed language, or low language confidence
- OpenAI (or configured) embeddings handle cross-script semantic retrieval
- BM25 uses Unicode-property tokens on both scripts in the same document

## Reindex after upgrades

Tokenizer or normalization changes invalidate keyword indexes. Run:

```bash
python -m app.cli.reindex_cli project --project-id <uuid> --full
```

## Related

- [text-chunking-for-rag.md](text-chunking-for-rag.md)
- [ocr-fundamentals.md](ocr-fundamentals.md)
- [hybrid-retrieval-journey.md](hybrid-retrieval-journey.md)
- ADR-010
