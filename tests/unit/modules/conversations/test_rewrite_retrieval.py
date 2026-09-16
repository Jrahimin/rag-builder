import uuid
from unittest.mock import AsyncMock

import pytest

from app.modules.conversations.ports import ContextChunk, ContextRetrievalResult
from app.modules.conversations.services.rewrite_retrieval import (
    retained_rewrite_question,
    retrieve_rewrite_context,
    rewrite_citation_ids,
    rewrite_followup_mode,
)
from app.modules.conversations.turn_resolution import (
    FollowupMode,
    HistoryMessage,
    TurnOutcome,
    TurnRelation,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "question,relation,eligible",
    [
        ("Translate the previous answer. Do not add new facts.", TurnRelation.FOLLOW_UP, True),
        ("Summarize it in three bullets.", TurnRelation.FOLLOW_UP, True),
        (
            "এটি তিনটি সংক্ষিপ্ত বাংলা বুলেটে বলুন। "
            "মূল দায়িত্ব, সময়সীমা এবং প্রযোজ্য সীমাবদ্ধতা রাখুন, সঙ্গে সূত্র দিন।",
            TurnRelation.FOLLOW_UP,
            True,
        ),
        ("Rewrite it and add current filing fees.", TurnRelation.FOLLOW_UP, True),
        ("এটি সংক্ষেপ করুন এবং বর্তমান ফি যোগ করুন।", TurnRelation.FOLLOW_UP, True),
        (
            "আগের উত্তরটি সহজ বাংলায় তিনটি বুলেটে বলুন। নতুন তথ্য যোগ করবেন না।",
            TurnRelation.FOLLOW_UP,
            True,
        ),
        ("Rewrite the previous answer and add current fees.", TurnRelation.FOLLOW_UP, True),
        ("Make it shorter.", TurnRelation.FOLLOW_UP, True),
        ("Summarize it and also keep citations.", TurnRelation.FOLLOW_UP, True),
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


@pytest.mark.parametrize(
    "question,mode",
    [
        ("Make it shorter.", FollowupMode.PRESENTATION_ONLY),
        ("Summarize it and also keep citations.", FollowupMode.PRESENTATION_ONLY),
        ("Summarize it and include filing penalties.", FollowupMode.ADDS_FACTS),
        ("What are the filing penalties?", FollowupMode.NOT_APPLICABLE),
    ],
)
def test_rewrite_followup_mode_distinguishes_presentation_from_added_facts(question, mode):
    assert (
        rewrite_followup_mode(question, TurnOutcome.RESOLVED, TurnRelation.FOLLOW_UP)
        is mode
    )


def test_explicit_fact_preserving_rewrite_overrides_incorrect_resolver_mode():
    question = (
        "Rewrite that as exactly three short bullets in English. "
        "Keep the same facts and citations."
    )
    assert rewrite_followup_mode(
        question,
        TurnOutcome.RESOLVED,
        TurnRelation.FOLLOW_UP,
        FollowupMode.ADDS_FACTS,
    ) is FollowupMode.PRESENTATION_ONLY


def test_chained_rewrite_keeps_the_last_factual_user_topic():
    history = [
        HistoryMessage(id=uuid.uuid4(), role="user", content="What goods may be sold?"),
        HistoryMessage(id=uuid.uuid4(), role="assistant", content="Existing or future goods."),
        HistoryMessage(id=uuid.uuid4(), role="user", content="Make it shorter."),
        HistoryMessage(id=uuid.uuid4(), role="assistant", content="Existing or future goods."),
    ]
    assert retained_rewrite_question(history) == "What goods may be sold?"


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


async def test_mixed_rewrite_reserves_space_for_prior_proof_and_new_search_results():
    cited = [
        ContextChunk(uuid.uuid4(), uuid.uuid4(), i, f"Prior {i}", 0.9, "Guide", f"old-{i}")
        for i in range(4)
    ]
    fresh = [
        ContextChunk(uuid.uuid4(), uuid.uuid4(), i, f"New {i}", 0.8, "Fees", f"new-{i}")
        for i in range(4)
    ]
    retrieval = AsyncMock()
    retrieval.supports_cited_retrieval = True
    retrieval.retrieve.side_effect = [
        ContextRetrievalResult(cited, {"snapshot": "same"}),
        ContextRetrievalResult(fresh, {"snapshot": "same"}),
    ]
    result = await retrieve_rewrite_context(
        retrieval,
        seeds=[item.chunk_id for item in cited],
        request={"query": "add filing penalties", "top_k": 4},
        mode=FollowupMode.ADDS_FACTS,
    )
    assert result.chunks[:2] == cited[:2]
    assert result.chunks[2:] == fresh[:2]
    assert result.diagnostics["rewrite_recall"]["status"] == "mixed_cited_and_search"
