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
2. Non-Bangla PDF image-only or sparse pages use the general fallback (Paddle when configured)
3. Resolved Bangla PDFs OCR every page through Google Vision when the opt-in backend is configured
4. Per-page confidence and provider provenance are stored in parsed structure hints
5. **Language**: explicit `documents.ocr_lang`, then PDF script detection, then `APE_OCR__LANG`
6. **Provider pool**: `ocr_factory.get_ocr_provider(lang=...)` caches by effective backend and language

Install: `pip install -r backend/requirements/ocr.txt`

### Bangla OCR routing

Unicode Bangla PDFs auto-route to the opt-in Google Vision backend. Scans, images, and custom-font
Bangla PDFs cannot be auto-detected before OCR, so they require explicit `ocr_lang=bn` or deployment
default `bn`. See [multilingual support](../features/multilingual_support.md#bangla-bengali-ocr-routing-and-limitations).

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
