"""Parser lineage never borrows a repeated occurrence from another position."""

import hashlib
from dataclasses import replace

import pytest

from app.modules.knowledge.services.chunking.models import DraftChunk
from app.modules.knowledge.services.chunking.structural_provenance import (
    annotate_structure,
    bind_parser_elements,
)
from app.modules.knowledge.services.chunking.token_counting_service import TokenCountingService
from app.modules.knowledge.source_reconciliation import governance_proposals
from app.platform.providers.contracts.document_parser import (
    ParsedDocument,
    ParsedElement,
    ParsedElementType,
)

pytestmark = pytest.mark.unit


def parsed():
    text = "Heading\n\nRepeated clause\n\nHeading\n\nRepeated clause"
    starts = [(0, 7), (9, 24), (26, 33), (35, 50)]
    return ParsedDocument(
        text=text,
        page_count=2,
        parser_name="fixture",
        parser_version="1",
        elements=tuple(
            ParsedElement(
                text=text[a:b],
                element_type=ParsedElementType.HEADING
                if i % 2 == 0
                else ParsedElementType.PARAGRAPH,
                char_start=a,
                char_end=b,
                page_start=i // 2 + 1,
            )
            for i, (a, b) in enumerate(starts)
        ),
    )


def annotate(chunks, source):
    annotate_structure(chunks, source, max_tokens=400, token_counter=TokenCountingService())


def test_repeated_parser_text_keeps_distinct_identity_and_source_location():
    source = bind_parser_elements(parsed())
    first, second = source.elements[1], source.elements[3]
    assert first.text == second.text
    assert (
        first.metadata["source_spans"][0]["element_id"]
        != second.metadata["source_spans"][0]["element_id"]
    )
    chunk = DraftChunk(
        content="Heading\n" + second.text,
        char_start=0,
        char_end=len("Heading\n" + second.text),
        metadata={
            "source_spans": [
                *source.elements[2].metadata["source_spans"],
                *second.metadata["source_spans"],
            ]
        },
    )
    annotate([chunk], source)
    assert chunk.char_start is None
    assert [s["char_start"] for s in chunk.metadata["source_spans"]] == [26, 35]
    assert chunk.metadata["provenance_precision"] == "chunk_with_source_spans"
    assert all(
        s["span_hash"] == hashlib.sha256(s["text"].encode()).hexdigest()
        for s in chunk.metadata["source_spans"]
    )


def test_unbound_or_wrong_parser_spans_are_not_reconstructed_by_text_search():
    source = parsed()
    bad = replace(source.elements[3], char_start=3, char_end=18)
    bound = bind_parser_elements(replace(source, elements=(bad,)))
    assert bound.elements[0].metadata["source_spans"] == []
    chunk = DraftChunk(
        content="Repeated clause",
        metadata={
            "source_spans": [
                {"element_id": "bad", "text": "Repeated clause", "char_start": 3, "char_end": 18}
            ]
        },
    )
    annotate([chunk], source)
    assert chunk.metadata["source_spans"] == []
    assert chunk.metadata["provenance_precision"] == "unattested"


def test_ingestion_proposals_remain_untrusted():
    proposals = governance_proposals(
        content="Section 21 is amended.",
        source_spans=[{"text": "Section 21 is amended.", "provenance": "exact_source_span"}],
        scope_facts=[],
    )
    assert proposals and all(p["status"] == "untrusted" for p in proposals)


@pytest.mark.parametrize("corruption", [None, "hash", "offset", "element", "text"])
def test_scope_review_accepts_only_attested_noncontiguous_constituents(corruption):
    from types import SimpleNamespace

    from app.modules.knowledge.services.scope_review_service import _attested_review_span
    from app.platform.domain.content_hash import content_hash

    quote = "Assessment Year 2026-27"
    span = {
        "text": quote,
        "char_start": 80,
        "char_end": 80 + len(quote),
        "provenance": "exact_source_span",
    }
    attestation = {**span, "span_hash": content_hash(quote), "element_id": "p7"}
    if corruption == "hash":
        attestation["span_hash"] = "bad"
    if corruption == "offset":
        attestation["char_start"] = 81
    if corruption == "element":
        attestation["element_id"] = None
    if corruption == "text":
        attestation["text"] = "foreign"
    chunk = SimpleNamespace(
        char_start=None, content="Heading\n" + quote, chunk_metadata={"source_spans": [attestation]}
    )
    assert bool(_attested_review_span(chunk, span)) is (corruption is None)
    contiguous = SimpleNamespace(char_start=80, content=quote, chunk_metadata={})
    assert _attested_review_span(contiguous, span)
