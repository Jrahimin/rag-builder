"""Plain text and Markdown document parser."""

from __future__ import annotations

import re

from app.platform.domain.language_detection import detect_language
from app.platform.domain.text_normalizer import normalize_for_storage
from app.platform.providers.contracts.document_parser import (
    BaseDocumentParserProvider,
    ParsedDocument,
    ParsedElement,
    ParsedElementType,
    SourceFormat,
)
from app.platform.providers.implementations.parsed_element_builder import finalize_elements

_PARSER_NAME = "plain_text"
_PARSER_VERSION = "2.0.0"
_TEXT_EXTENSIONS = {".txt", ".md", ".markdown"}
_MARKDOWN_EXTENSIONS = {".md", ".markdown"}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_LIST_RE = re.compile(r"^(\s*[-*+]|\s*\d+[.)])\s+")
_FENCE_RE = re.compile(r"^```")
_TABLE_ROW_RE = re.compile(r"^\|.+\|$")


class PlainTextParserProvider(BaseDocumentParserProvider):
    """Decode UTF-8 and emit structured elements for text and markdown files."""

    def parse(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str | None,
        ocr_lang: str | None = None,
    ) -> ParsedDocument:
        del content_type, ocr_lang
        source_format = _detect_source_format(filename)
        if not data:
            return ParsedDocument(
                text="",
                page_count=0,
                parser_name=_PARSER_NAME,
                parser_version=_PARSER_VERSION,
                source_format=source_format,
                parser_confidence=0.0,
            )

        try:
            text = data.decode("utf-8")
            warnings: tuple[str, ...] = ()
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
            warnings = ("Non-UTF-8 bytes were replaced during decoding.",)

        if source_format is SourceFormat.MARKDOWN:
            elements = _parse_markdown_elements(text)
            confidence = 0.9 if any(element.element_type != ParsedElementType.PARAGRAPH for element in elements) else 0.7
        else:
            elements = _parse_plain_text_elements(text)
            confidence = 0.6

        projection, finalized = finalize_elements(elements)
        language_result = detect_language(projection)
        return ParsedDocument(
            text=projection,
            page_count=1 if projection.strip() else 0,
            parser_name=_PARSER_NAME,
            parser_version=_PARSER_VERSION,
            elements=finalized,
            source_format=source_format,
            parser_confidence=confidence,
            language=language_result.primary_language,
            warnings=warnings,
            structure_hints={
                "line_count": len(text.splitlines()),
                "language_confidence": language_result.confidence,
                "languages": language_result.languages,
                "is_mixed": language_result.is_mixed,
            },
        )

    @staticmethod
    def supports(filename: str, content_type: str | None) -> bool:
        lower = filename.lower()
        if any(lower.endswith(ext) for ext in _TEXT_EXTENSIONS):
            return True
        return content_type in {"text/plain", "text/markdown"}


def _detect_source_format(filename: str) -> SourceFormat:
    lower = filename.lower()
    if any(lower.endswith(ext) for ext in _MARKDOWN_EXTENSIONS):
        return SourceFormat.MARKDOWN
    return SourceFormat.PLAIN_TEXT


def _parse_plain_text_elements(text: str) -> list[ParsedElement]:
    elements: list[ParsedElement] = []
    for block in re.split(r"\n\s*\n", text):
        stripped = normalize_for_storage(block)
        if stripped:
            elements.append(
                ParsedElement(
                    text=stripped,
                    element_type=ParsedElementType.PARAGRAPH,
                    page_start=1,
                    page_end=1,
                )
            )
    return elements


def _parse_markdown_elements(text: str) -> list[ParsedElement]:
    elements: list[ParsedElement] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        if _FENCE_RE.match(stripped):
            code_lines = [stripped]
            index += 1
            while index < len(lines) and not _FENCE_RE.match(lines[index].strip()):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                code_lines.append(lines[index].strip())
                index += 1
            elements.append(
                ParsedElement(
                    text="\n".join(code_lines),
                    element_type=ParsedElementType.CODE_BLOCK,
                    page_start=1,
                    page_end=1,
                )
            )
            continue

        heading_match = _HEADING_RE.match(stripped)
        if heading_match:
            level = len(heading_match.group(1))
            elements.append(
                ParsedElement(
                    text=heading_match.group(2).strip(),
                    element_type=ParsedElementType.HEADING,
                    page_start=1,
                    page_end=1,
                    heading_level=level,
                )
            )
            index += 1
            continue

        if _TABLE_ROW_RE.match(stripped):
            table_lines = [stripped]
            index += 1
            while index < len(lines) and _TABLE_ROW_RE.match(lines[index].strip()):
                table_lines.append(lines[index].strip())
                index += 1
            elements.append(
                ParsedElement(
                    text="\n".join(table_lines),
                    element_type=ParsedElementType.TABLE,
                    page_start=1,
                    page_end=1,
                )
            )
            continue

        if _LIST_RE.match(line):
            list_lines = [stripped]
            index += 1
            while index < len(lines):
                next_line = lines[index]
                if not next_line.strip():
                    break
                if _LIST_RE.match(next_line) or (next_line.startswith("  ") and list_lines):
                    list_lines.append(next_line.strip())
                    index += 1
                else:
                    break
            elements.append(
                ParsedElement(
                    text="\n".join(list_lines),
                    element_type=ParsedElementType.LIST,
                    page_start=1,
                    page_end=1,
                )
            )
            continue

        paragraph_lines = [stripped]
        index += 1
        while index < len(lines):
            next_line = lines[index]
            if not next_line.strip():
                break
            if (
                _HEADING_RE.match(next_line.strip())
                or _FENCE_RE.match(next_line.strip())
                or _TABLE_ROW_RE.match(next_line.strip())
                or _LIST_RE.match(next_line)
            ):
                break
            paragraph_lines.append(next_line.strip())
            index += 1
        elements.append(
            ParsedElement(
                text=" ".join(paragraph_lines),
                element_type=ParsedElementType.PARAGRAPH,
                page_start=1,
                page_end=1,
            )
        )

    return elements
