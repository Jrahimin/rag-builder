"""Split tables retain exact carried row occurrences through final review."""

from types import SimpleNamespace

import pytest

from app.core.config import ChunkingConfig, ChunkingStrategy
from app.modules.knowledge.services.chunking.chunk_strategies import (
    recursive_fallback_chunk_strategy,
)
from app.modules.knowledge.services.chunking.chunk_strategies.structure_helpers import pack_elements
from app.modules.knowledge.services.chunking.models import (
    ChunkingContext,
    StructureAnalysis,
    StructureSignals,
)
from app.modules.knowledge.services.chunking.structural_provenance import (
    annotate_structure,
    bind_parser_elements,
)
from app.modules.knowledge.services.chunking.token_counting_service import TokenCountingService
from app.modules.knowledge.services.scope_review_service import _attested_review_span
from app.platform.providers.contracts.document_parser import (
    ParsedDocument,
    ParsedElement,
    ParsedElementType,
)

pytestmark = pytest.mark.unit


def split_table(oversized=False):
    header = "Category | Rate"
    repeated = "Assessment Year 2025-26 | 10% | " + " ".join(["qualifier"] * 55)
    if oversized:
        repeated += " | " + " ".join(["qualifier"] * 120)
    rows = [repeated, "Assessment Year 2026-27 | 15% | " + " ".join(["different"] * 55), repeated]
    text = "\n".join([header, *rows])
    source = bind_parser_elements(
        ParsedDocument(
            text=text,
            page_count=1,
            parser_name="fixture",
            parser_version="1",
            elements=(
                ParsedElement(
                    text=text,
                    element_type=ParsedElementType.TABLE,
                    char_start=0,
                    char_end=len(text),
                    metadata={"table_header": header, "table_rows": rows},
                ),
            ),
        )
    )
    counter = TokenCountingService()
    config = ChunkingConfig(target_tokens=75, max_tokens=100, min_tokens=1)
    context = ChunkingContext(
        parsed=source,
        config=config,
        analysis=StructureAnalysis(1.0, StructureSignals(has_tables=True)),
        strategy=ChunkingStrategy.STRUCTURE,
    )
    chunks = pack_elements(
        list(source.elements),
        context=context,
        token_counter=counter,
        fallback=recursive_fallback_chunk_strategy.RecursiveFallbackChunkStrategy(
            token_counter=counter
        ),
        strategy_name="fixture",
    )
    annotate_structure(chunks, source, max_tokens=100, token_counter=counter)
    repeated_spans = [
        span
        for span in source.elements[0].metadata["source_spans"]
        if span["text"] == repeated.strip()
    ]
    assert len(repeated_spans) == 2
    return source, chunks, repeated_spans


@pytest.mark.parametrize("oversized", [False, True])
def test_group_never_attests_identical_sibling_row(oversized):
    source, chunks, repeated = split_table(oversized)
    first = [chunk for chunk in chunks if chunk.metadata["table_row_start"] == 0]
    last = [chunk for chunk in chunks if chunk.metadata["table_row_start"] == 2]
    assert first and last
    for group, sibling in [(first, repeated[1]), (last, repeated[0])]:
        for chunk in group:
            assert all(
                not (
                    span["char_start"] == sibling["char_start"]
                    and span["char_end"] == sibling["char_end"]
                )
                for span in chunk.metadata["source_spans"]
            )
            review = SimpleNamespace(
                char_start=chunk.char_start, content=chunk.content, chunk_metadata=chunk.metadata
            )
            assert not _attested_review_span(review, sibling)
    if not oversized:
        for chunk, own in [(first[0], repeated[0]), (last[0], repeated[1])]:
            review = SimpleNamespace(
                char_start=chunk.char_start, content=chunk.content, chunk_metadata=chunk.metadata
            )
            assert _attested_review_span(review, own)
            assert any(span["text"] == "Category | Rate" for span in chunk.metadata["source_spans"])
    else:
        assert all(
            chunk.metadata["table_context_status"] == "context_exceeds_budget"
            for chunk in first + last
        )
        assert all(
            source.text[span["char_start"] : span["char_end"]] == span["text"]
            for chunk in chunks
            for span in chunk.metadata["source_spans"]
        )


@pytest.mark.parametrize("boundary", ["annotation", "review"])
def test_out_of_group_carried_span_is_rejected_even_if_inventory_is_polluted(boundary):
    from app.platform.domain.content_hash import content_hash

    source, chunks, repeated = split_table()
    first = next(chunk for chunk in chunks if chunk.metadata["table_row_start"] == 0)
    sibling = {**repeated[1], "span_hash": content_hash(repeated[1]["text"])}
    first.metadata["source_spans"].append(sibling)
    # A misleading contiguous locator cannot bypass the split-group ownership rule.
    first.char_start = sibling["char_start"] - first.content.index(sibling["text"])
    if boundary == "annotation":
        annotate_structure([first], source, max_tokens=100, token_counter=TokenCountingService())
        assert sibling not in first.metadata["source_spans"]
    review = SimpleNamespace(
        char_start=first.char_start, content=first.content, chunk_metadata=first.metadata
    )
    assert not _attested_review_span(review, sibling)


def test_split_group_without_carried_rows_cannot_reconstruct_sibling_from_locator():
    from app.modules.knowledge.services.chunking.models import DraftChunk

    source, chunks, _repeated = split_table()
    last = next(chunk for chunk in chunks if chunk.metadata["table_row_start"] == 2)
    empty = DraftChunk(
        content=last.content,
        char_start=0,
        char_end=len(last.content),
        metadata={**last.metadata, "source_spans": []},
    )
    annotate_structure([empty], source, max_tokens=100, token_counter=TokenCountingService())
    assert empty.char_start is None
    assert empty.metadata["source_spans"] == []
    assert empty.metadata["provenance_precision"] == "unattested"
