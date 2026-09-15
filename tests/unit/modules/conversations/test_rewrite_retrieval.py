import uuid
from unittest.mock import AsyncMock

import pytest

from app.modules.conversations.ports import ContextChunk, ContextRetrievalResult
from app.modules.conversations.services.rewrite_retrieval import (
    retrieve_rewrite_context,
    rewrite_citation_ids,
)
from app.modules.conversations.turn_resolution import HistoryMessage, TurnOutcome, TurnRelation

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "question,relation,eligible",
    [
        ("Translate the previous answer. Do not add new facts.", TurnRelation.FOLLOW_UP, True),
        (
            "আগের উত্তরটি সহজ বাংলায় তিনটি বুলেটে বলুন। নতুন তথ্য যোগ করবেন না।",
            TurnRelation.FOLLOW_UP,
            True,
        ),
        ("Explain the previous answer and add current fees.", TurnRelation.FOLLOW_UP, False),
        ("Translate the previous answer. Do not add new facts.", TurnRelation.CORRECTION, False),
    ],
)
def test_rewrite_recall_requires_explicit_fact_preserving_followup(question, relation, eligible):
    previous_id, current_id, chunk_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    history = [HistoryMessage(id=previous_id, role="assistant", content="Previous answer")]
    citations = {
        previous_id: [
            {"chunk_id": str(chunk_id)},
            {"chunk_id": str(chunk_id)},
            {"chunk_id": "invalid"},
            {"source_kind": "web", "chunk_id": str(uuid.uuid4())},
        ]
    }
    assert rewrite_citation_ids(question, TurnOutcome.RESOLVED, relation, history, citations) == (
        [chunk_id] if eligible else []
    )
    assert not rewrite_citation_ids(question, TurnOutcome.FALLBACK, relation, history, citations)
    # Never reach past the actual preceding answer into an older cited message.
    history.append(HistoryMessage(id=current_id, role="assistant", content="No citations"))
    assert not rewrite_citation_ids(question, TurnOutcome.RESOLVED, relation, history, citations)


@pytest.mark.parametrize("empty", [False, True])
async def test_rewrite_recall_keeps_filters_and_falls_back_only_when_empty(empty):
    chunk_id = uuid.uuid4()
    chunk = ContextChunk(chunk_id, uuid.uuid4(), 0, "Current source", 0.9, "Guide", "hash")
    primary = ContextRetrievalResult(chunks=[] if empty else [chunk], diagnostics={})
    fallback = ContextRetrievalResult(chunks=[chunk], diagnostics={})
    retrieval = AsyncMock()
    retrieval.supports_cited_retrieval = True
    retrieval.retrieve.side_effect = [primary, fallback]
    request = {
        "query": "Original topic",
        "top_k": 5,
        "document_id": chunk.document_id,
        "metadata_filter": {"region": "Dhaka"},
        "as_of": "2026-01-01",
    }
    result = await retrieve_rewrite_context(retrieval, seeds=[chunk_id], request=request)
    assert retrieval.retrieve.call_args_list[0].kwargs == {**request, "cited_chunk_ids": [chunk_id]}
    assert retrieval.retrieve.await_count == (2 if empty else 1)
    if empty:
        assert retrieval.retrieve.call_args_list[1].kwargs == request
    assert result.chunks == [chunk]
    assert result.diagnostics["rewrite_recall"]["status"] == (
        "fallback_search" if empty else "cited_passages"
    )


async def test_legacy_retrieval_port_does_not_receive_new_keyword():
    retrieval = AsyncMock()
    retrieval.supports_cited_retrieval = False
    retrieval.retrieve.return_value = ContextRetrievalResult([], {})
    await retrieve_rewrite_context(
        retrieval, seeds=[uuid.uuid4()], request={"query": "topic", "top_k": 5}
    )
    retrieval.retrieve.assert_awaited_once_with(query="topic", top_k=5)
