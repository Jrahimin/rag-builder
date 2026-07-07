# Multilingual Document Processing

APE treats multilingual corpora as first-class through Unicode-property tokenization, shared text normalization, optional OCR, and language confidence metadata.

## Capabilities

| Area | Behavior |
|------|----------|
| Tokenization | `regex` package with `\p{Letter}`, `\p{Number}`, `\p{Mark}` (`unicode_property_v1`) |
| Normalization | Shared `text_normalizer` for parsers, chunking, BM25, and query paths |
| Languages | Heuristic script-ratio detection with confidence and mixed-language support |
| OCR | Optional `OCRProvider` (PaddleOCR via `requirements/ocr.txt`) |
| PDF parsing | Page-level Unicode quality scoring with PyMuPDF → PDFium → OCR fallback |
| FTS | Configurable `APE_RETRIEVAL__FTS_REGCONFIG` (default `simple`) |
| Reindex | `python -m app.cli.reindex_cli` after tokenizer upgrades |

## Configuration

```env
APE_CHUNKING__TOKEN_COUNT_METHOD=unicode_property_v1
APE_OCR__ENABLED=false
APE_OCR__BACKEND=noop
APE_OCR__LANG=en
APE_RETRIEVAL__FTS_REGCONFIG=simple
APE_RETRIEVAL__MIN_OCR_CONFIDENCE=
APE_RETRIEVAL__FILTERABLE_METADATA_KEYS=source,tags,ocr_confidence
APE_PARSING__PDF_TEXT_PARSERS=pymupdf,pdfium
APE_PARSING__MIN_PAGE_QUALITY_SCORE=0.55
APE_PARSING__MIN_DOCUMENT_SUCCESS_RATIO=0.2
```

Enable OCR:

```bash
pip install -r backend/requirements/ocr.txt
```

Set `APE_OCR__ENABLED=true` and `APE_OCR__BACKEND=paddle`.

### Per-document OCR language

| Source | Precedence |
|--------|------------|
| `ocr_lang` on upload (form field) or reprocess (query) | Highest — stored on `documents.ocr_lang` |
| `APE_OCR__LANG` | Deployment default when `documents.ocr_lang` is null |

Use this when a single deployment ingests mixed scripts (e.g. default `en` for English scans, `ocr_lang=hi` for Hindi/Devanagari image uploads). Aliases are normalized at ingest (`eng`→`en`, `bangla`→`bn`).

The worker keeps a small in-process OCR provider pool (keyed by backend + language + GPU flag) so multiple languages do not reload Paddle models on every document.

## Known limitation: Bangla (Bengali) OCR

**Phase 1 does not reliably extract Bangla from scanned or custom-font PDFs.** This is a documented platform limitation, not a configuration bug.

| Scenario | What happens today |
| -------- | ------------------ |
| Digital PDF with valid Unicode Bengali text layer | Works — PyMuPDF/PDFium extract `\p{Bengali}`; chunking, BM25, and semantic search behave normally |
| Legacy Bangla PDF with broken `/ToUnicode` (custom fonts) | Native extract is Latin glyph soup; parse quality scorer rejects it and OCR fallback runs |
| `ocr_lang=bn` on upload/reprocess | **Fails** — PaddleOCR 3.7 does not ship stock Bengali models; worker raises `ProviderError` at provider init (`paddle_ocr_langs.py`) |
| OCR fallback with default `APE_OCR__LANG=en` | Runs, but English recognition on Bangla script produces wrong output (CJK misreads, number fragments, readable English blocks only). May still pass OCR confidence and parse-quality checks |
| Bangla image upload (`.png`, `.jpg`) with `ocr_lang=bn` | Same as above — `bn` is not supported on the Paddle backend |

**What works for Bangla corpora today**

- PDFs that already embed proper Unicode Bengali in the text layer
- Plain text / DOCX with Bengali content
- Any ingestion path where parsed text contains real `\p{Bengali}` characters

**Future direction (Phase 1+ / Phase 2)**

- Alternate `OCRProvider` implementation with Bengali support (e.g. Tesseract `ben`, cloud Vision API)
- Custom PaddleOCR Bengali recognition model wired through the existing provider factory
- Script-mismatch validation so wrong-language OCR cannot beat native parser candidates

See ADR-010, ADR-011, [OCR fundamentals](../learning/ocr-fundamentals.md), and `platform/providers/implementations/paddle_ocr_langs.py`.

### PDF mixed-content handling

| Setting | Default | Effect |
|---------|---------|--------|
| `APE_OCR__MIN_TEXT_CHARS` | `20` | Discard short OCR noise (e.g. logo misreads) |
| `APE_OCR__MIN_PAGE_CONFIDENCE` | `0.3` | Discard low-confidence OCR |
| `APE_OCR__MIN_IMAGE_AREA_RATIO` | `0.08` | Per-image OCR only for images ≥ 8% of page area |

On pages with **both** native text and embedded images: the PDF extraction workflow scores native text first, tries PDFium only for degraded pages, and invokes OCR only when configured and still below quality threshold. OCR output is kept only when it beats the best parser candidate for that page.

## Reindex after upgrades

```bash
python -m app.cli.reindex_cli document --project-id <uuid> --document-id <uuid>
python -m app.cli.reindex_cli project --project-id <uuid> --full
python -m app.cli.reindex_cli project --project-id <uuid> --dry-run
```

## Acceptance scenarios

- Unicode Bengali text (valid text layer or `.txt`/`.docx`) + Bangla query → non-zero tokens; hybrid retrieval returns relevant chunks
- English-only and mixed-language documents behave symmetrically
- Low OCR confidence chunks filterable via `APE_RETRIEVAL__MIN_OCR_CONFIDENCE`
- Ellipsis-terminated OCR lines split on sentence boundaries
- Legacy Bangla scan / custom-font PDF → parse quality gate triggers OCR, but **Bangla OCR output is not production-ready** until a Bengali-capable OCR backend is added (see limitation above)

See ADR-010, ADR-011, and `docs/learning/multilingual-text-processing.md`.
