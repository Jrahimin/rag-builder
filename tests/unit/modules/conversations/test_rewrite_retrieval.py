import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.modules.conversations.citation_snapshots import EVIDENCE_PROVENANCE_VERSION
from app.modules.conversations.ports import ContextChunk, ContextRetrievalResult
from app.modules.conversations.services.rewrite_retrieval import (
    request_scope_conflicts,
    retained_rewrite_question,
    retrieve_rewrite_context,
    rewrite_citation_ids,
    rewrite_followup_mode,
    try_presentation_preflight,
    used_citations_include_web,
)
from app.modules.conversations.turn_resolution import (
    FollowupMode,
    HistoryMessage,
    RequestFilters,
    TurnOutcome,
    TurnRelation,
    TurnResolutionInput,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "question,relation,mode",
    [
        (
            "Translate the previous answer. Do not add new facts.",
            TurnRelation.FOLLOW_UP,
            FollowupMode.PRESENTATION_ONLY,
        ),
        ("Summarize it in three bullets.", TurnRelation.FOLLOW_UP, FollowupMode.PRESENTATION_ONLY),
        (
            "Summarize it. Keep the original facts and citations.",
            TurnRelation.FOLLOW_UP,
            FollowupMode.PRESENTATION_ONLY,
        ),
        (
            "এটি তিনটি সংক্ষিপ্ত বাংলা বুলেটে বলুন। মূল তথ্য ও সূত্র রাখুন।",
            TurnRelation.FOLLOW_UP,
            FollowupMode.PRESENTATION_ONLY,
        ),
        (
            "Rewrite it and add current filing fees.",
            TurnRelation.FOLLOW_UP,
            FollowupMode.ADDS_FACTS,
        ),
        (
            "এটি সংক্ষেপ করুন এবং বর্তমান ফি যোগ করুন।",
            TurnRelation.FOLLOW_UP,
            FollowupMode.ADDS_FACTS,
        ),
        (
            "আগের উত্তরটি সহজ বাংলায় তিনটি বুলেটে বলুন। নতুন তথ্য যোগ করবেন না।",
            TurnRelation.FOLLOW_UP,
            FollowupMode.PRESENTATION_ONLY,
        ),
        (
            "উপরের উত্তরটি নতুন কোনো তথ্য যোগ না করে ৩টি বুলেট পয়েন্টে লিখুন।",
            TurnRelation.FOLLOW_UP,
            FollowupMode.PRESENTATION_ONLY,
        ),
        (
            "আগের উত্তরের তথ্য অপরিবর্তিত রেখে এক বাক্যে লিখুন।",
            TurnRelation.FOLLOW_UP,
            FollowupMode.PRESENTATION_ONLY,
        ),
        (
            "Rewrite the previous answer and add current fees.",
            TurnRelation.FOLLOW_UP,
            FollowupMode.ADDS_FACTS,
        ),
        ("Make it shorter.", TurnRelation.FOLLOW_UP, FollowupMode.PRESENTATION_ONLY),
        (
            "Summarize it and also keep citations.",
            TurnRelation.FOLLOW_UP,
            FollowupMode.PRESENTATION_ONLY,
        ),
        (
            "Translate the previous answer. Do not add new facts.",
            TurnRelation.CORRECTION,
            FollowupMode.NOT_APPLICABLE,
        ),
    ],
)
def test_rewrite_recall_requires_explicit_fact_preserving_followup(question, relation, mode):
    previous_id, current_id, chunk_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    history = [HistoryMessage(id=previous_id, role="assistant", content="Previous answer [1]")]
    citations = {
        previous_id: [
            {"chunk_id": str(chunk_id)},
            {"chunk_id": str(chunk_id)},
            {"chunk_id": "invalid"},
            {"source_kind": "web", "chunk_id": str(uuid.uuid4())},
        ]
    }
    assert rewrite_followup_mode(question, TurnOutcome.RESOLVED, relation) is mode
    assert rewrite_citation_ids(question, TurnOutcome.RESOLVED, relation, history, citations) == (
        [chunk_id] if mode is not FollowupMode.NOT_APPLICABLE else []
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
        ("Rewrite it and compare the two filing periods.", FollowupMode.ADDS_FACTS),
        ("Translate the previous answer and calculate the penalty.", FollowupMode.ADDS_FACTS),
        (
            "Summarize it, do not add anything, but what are the current fees?",
            FollowupMode.ADDS_FACTS,
        ),
        (
            "Summarize it. Do not add new facts, but do these rules still apply?",
            FollowupMode.ADDS_FACTS,
        ),
        (
            "এটি সংক্ষেপ করুন, নতুন তথ্য যোগ করবেন না, কিন্তু বর্তমান ফি কত?",
            FollowupMode.ADDS_FACTS,
        ),
        (
            "এটি সংক্ষেপ করুন, যোগ করবেন না, কিন্তু বর্তমান নিয়ম কি এখনও প্রযোজ্য?",
            FollowupMode.ADDS_FACTS,
        ),
        ("Do these rules still apply?", FollowupMode.NOT_APPLICABLE),
        ("Does this currently apply?", FollowupMode.NOT_APPLICABLE),
        ("What are the filing penalties?", FollowupMode.NOT_APPLICABLE),
        ("Summarize penalties", FollowupMode.NOT_APPLICABLE),
        ("Rewrite it for minors", FollowupMode.NOT_APPLICABLE),
        ("Summarize it for partnerships", FollowupMode.NOT_APPLICABLE),
    ],
)
def test_rewrite_followup_mode_distinguishes_presentation_from_added_facts(question, mode):
    assert rewrite_followup_mode(question, TurnOutcome.RESOLVED, TurnRelation.FOLLOW_UP) is mode


def test_explicit_fact_preserving_rewrite_overrides_incorrect_resolver_mode():
    question = (
        "Rewrite that as exactly three short bullets in English. Keep the same facts and citations."
    )
    assert (
        rewrite_followup_mode(
            question,
            TurnOutcome.RESOLVED,
            TurnRelation.FOLLOW_UP,
            FollowupMode.ADDS_FACTS,
        )
        is FollowupMode.PRESENTATION_ONLY
    )


def test_rewrite_recall_uses_only_markers_rendered_in_the_previous_answer():
    previous_id = uuid.uuid4()
    uncited, cited = uuid.uuid4(), uuid.uuid4()
    history = [
        HistoryMessage(
            id=previous_id,
            role="assistant",
            content="The concise supported point uses the second passage. [2]",
        )
    ]
    citations = {
        previous_id: [
            {"chunk_id": str(uncited)},
            {"chunk_id": str(cited)},
        ]
    }

    assert rewrite_citation_ids(
        "Make it shorter.",
        TurnOutcome.STANDALONE,
        TurnRelation.TOPIC_CHANGE,
        history,
        citations,
        mode=FollowupMode.PRESENTATION_ONLY,
    ) == [cited]


def test_rewrite_citation_ids_include_recalled_modifier_dependencies():
    previous_id = uuid.uuid4()
    base_id, modifier_id = uuid.uuid4(), uuid.uuid4()
    relationship_id = uuid.uuid4()
    modifier_revision = uuid.uuid4()
    history = [
        HistoryMessage(
            id=previous_id,
            role="assistant",
            content="The rebate remains 15% for other provisions. [1]",
        )
    ]
    citations = {
        previous_id: [
            {
                "chunk_id": str(base_id),
                "source_kind": "knowledge",
                "source_revision_id": str(uuid.uuid4()),
                "authority_dependencies": [
                    {
                        "relationship_id": str(relationship_id),
                        "modifier_revision_id": str(modifier_revision),
                        "outcome": "expanded",
                        "modifier_recalled": True,
                    }
                ],
            },
            {
                "chunk_id": str(modifier_id),
                "source_kind": "knowledge",
                "source_revision_id": str(modifier_revision),
                "authority_dependencies": [
                    {"relationship_id": str(relationship_id), "modifier_recalled": True}
                ],
            },
        ]
    }
    assert rewrite_citation_ids(
        "Make it shorter.",
        TurnOutcome.RESOLVED,
        TurnRelation.FOLLOW_UP,
        history,
        citations,
        mode=FollowupMode.PRESENTATION_ONLY,
    ) == [base_id, modifier_id]


def test_rewrite_citation_ids_seed_recorded_modifier_chunk_without_extra_citation():
    previous_id = uuid.uuid4()
    base_id, modifier_id = uuid.uuid4(), uuid.uuid4()
    history = [
        HistoryMessage(
            id=previous_id,
            role="assistant",
            content="The rebate cannot exceed tax liability. [1]",
        )
    ]
    citations = {
        previous_id: [
            {
                "chunk_id": str(base_id),
                "source_kind": "knowledge",
                "authority_dependencies": [
                    {
                        "relationship_id": str(uuid.uuid4()),
                        "modifier_revision_id": str(uuid.uuid4()),
                        "outcome": "expanded",
                        "modifier_recalled": True,
                        "modifier_chunk_id": str(modifier_id),
                    }
                ],
            }
        ]
    }
    assert rewrite_citation_ids(
        "Make it shorter.",
        TurnOutcome.RESOLVED,
        TurnRelation.FOLLOW_UP,
        history,
        citations,
        mode=FollowupMode.PRESENTATION_ONLY,
    ) == [base_id, modifier_id]


def test_chained_rewrite_keeps_the_last_factual_user_topic():
    history = [
        HistoryMessage(id=uuid.uuid4(), role="user", content="What goods may be sold?"),
        HistoryMessage(id=uuid.uuid4(), role="assistant", content="Existing or future goods."),
        HistoryMessage(id=uuid.uuid4(), role="user", content="Make it shorter."),
        HistoryMessage(id=uuid.uuid4(), role="assistant", content="Existing or future goods."),
    ]
    assert retained_rewrite_question(history) == "What goods may be sold?"


def test_chained_rewrite_uses_saved_resolver_lineage_for_ambiguous_transform():
    simplified_answer = uuid.uuid4()
    history = [
        HistoryMessage(id=uuid.uuid4(), role="user", content="What goods may be sold?"),
        HistoryMessage(id=uuid.uuid4(), role="assistant", content="Existing or future goods."),
        HistoryMessage(id=uuid.uuid4(), role="user", content="Explain that simply."),
        HistoryMessage(id=simplified_answer, role="assistant", content="Goods now or later."),
    ]
    metadata = {
        str(simplified_answer): {
            "turn_resolution": {
                "followup_mode": "presentation_only",
                "retained_factual_question": "What goods may be sold?",
            }
        }
    }
    assert retained_rewrite_question(history, metadata) == "What goods may be sold?"


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
        assert retrieval.retrieve.call_args_list[1].kwargs == {**request, "top_k": 3}
    assert result.chunks == [chunk]
    assert result.diagnostics["rewrite_recall"]["status"] == (
        "bounded_fallback_search" if empty else "cited_passages"
    )


async def test_legacy_retrieval_port_does_not_receive_new_keyword():
    retrieval = AsyncMock()
    retrieval.supports_cited_retrieval = False
    retrieval.retrieve.return_value = ContextRetrievalResult([], {})
    await retrieve_rewrite_context(
        retrieval, seeds=[uuid.uuid4()], request={"query": "topic", "top_k": 5}
    )
    retrieval.retrieve.assert_awaited_once_with(query="topic", top_k=5)


async def test_presentation_without_visible_citations_uses_a_small_labeled_fallback():
    retrieval = AsyncMock()
    retrieval.supports_cited_retrieval = True
    retrieval.retrieve.return_value = ContextRetrievalResult([], {})

    result = await retrieve_rewrite_context(
        retrieval,
        seeds=[],
        request={"query": "original topic", "top_k": 5},
        mode=FollowupMode.PRESENTATION_ONLY,
    )

    retrieval.retrieve.assert_awaited_once_with(query="original topic", top_k=3)
    assert result.chunks == []
    assert result.diagnostics["rewrite_recall"]["status"] == (
        "bounded_fallback_search_no_citations"
    )


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


def test_successive_presentation_rewrites_keep_presentation_mode_and_factual_topic():
    first = uuid.uuid4()
    second = uuid.uuid4()
    history = [
        HistoryMessage(id=uuid.uuid4(), role="user", content="What goods may be sold?"),
        HistoryMessage(id=first, role="assistant", content="Existing or future goods. [1]"),
        HistoryMessage(id=uuid.uuid4(), role="user", content="Summarize it in three bullets."),
        HistoryMessage(id=second, role="assistant", content="- Existing goods. [1]"),
    ]
    assert (
        rewrite_followup_mode(
            "Make it shorter.",
            TurnOutcome.RESOLVED,
            TurnRelation.FOLLOW_UP,
        )
        is FollowupMode.PRESENTATION_ONLY
    )
    assert retained_rewrite_question(history) == "What goods may be sold?"
    cited = uuid.uuid4()
    assert rewrite_citation_ids(
        "Make it shorter.",
        TurnOutcome.RESOLVED,
        TurnRelation.FOLLOW_UP,
        history,
        {second: [{"chunk_id": str(cited)}]},
    ) == [cited]


def test_presentation_mode_does_not_inspect_request_scope():
    question = "Make it shorter."
    assert (
        rewrite_followup_mode(question, TurnOutcome.RESOLVED, TurnRelation.FOLLOW_UP)
        is FollowupMode.PRESENTATION_ONLY
    )


async def test_adapter_without_exact_recall_keeps_cited_retrieve_fallback():
    retrieval = AsyncMock()
    retrieval.supports_cited_retrieval = True
    retrieval.supports_exact_recall = False
    chunk = ContextChunk(uuid.uuid4(), uuid.uuid4(), 0, "Current source", 0.9, "Guide", "hash")
    retrieval.retrieve.return_value = ContextRetrievalResult(chunks=[chunk], diagnostics={})
    seed = chunk.chunk_id
    result = await retrieve_rewrite_context(
        retrieval,
        seeds=[seed],
        request={"query": "topic", "top_k": 5},
        mode=FollowupMode.PRESENTATION_ONLY,
    )
    retrieval.retrieve.assert_awaited_once_with(query="topic", top_k=5, cited_chunk_ids=[seed])
    assert not hasattr(retrieval, "retrieve_exact") or retrieval.retrieve_exact.await_count == 0
    assert result.diagnostics["rewrite_recall"]["status"] == "cited_passages"


async def test_exact_recall_does_not_call_ranked_retrieve():
    retrieval = AsyncMock()
    retrieval.supports_cited_retrieval = True
    retrieval.supports_exact_recall = True
    chunk = ContextChunk(uuid.uuid4(), uuid.uuid4(), 0, "Current source", 0.9, "Guide", "hash")
    retrieval.retrieve_exact.return_value = ContextRetrievalResult(chunks=[chunk], diagnostics={})
    result = await retrieve_rewrite_context(
        retrieval,
        seeds=[chunk.chunk_id],
        request={"query": "topic", "top_k": 5},
        mode=FollowupMode.PRESENTATION_ONLY,
        prefer_exact=True,
    )
    retrieval.retrieve_exact.assert_awaited_once()
    retrieval.retrieve.assert_not_awaited()
    assert result.diagnostics["rewrite_recall"]["status"] == "exact_cited_passages"


async def test_empty_presentation_seeds_never_become_unrestricted_exact_recall():
    retrieval = AsyncMock()
    retrieval.supports_cited_retrieval = True
    retrieval.supports_exact_recall = True
    retrieval.retrieve.return_value = ContextRetrievalResult(chunks=[], diagnostics={})
    result = await retrieve_rewrite_context(
        retrieval,
        seeds=[],
        request={"query": "topic", "top_k": 8},
        mode=FollowupMode.PRESENTATION_ONLY,
        prefer_exact=True,
    )
    retrieval.retrieve_exact.assert_not_awaited()
    retrieval.retrieve.assert_awaited_once()
    assert retrieval.retrieve.await_args.kwargs["top_k"] == 3
    assert result.diagnostics["rewrite_recall"]["status"] == "bounded_fallback_search_no_citations"


def _preflight_payload(
    question: str, *, as_of: datetime | None = None
) -> tuple[
    TurnResolutionInput,
    dict[uuid.UUID, list[dict[str, object]]],
    uuid.UUID,
]:
    user_id = uuid.uuid4()
    assistant_id = uuid.uuid4()
    history = [
        HistoryMessage(id=user_id, role="user", content="What is the refund period?"),
        HistoryMessage(
            id=assistant_id,
            role="assistant",
            content="Refunds are due within 30 days. [1]",
        ),
    ]
    citations = {
        assistant_id: [
            {
                "chunk_id": str(uuid.uuid4()),
                "source_kind": "knowledge",
                "evidence_provenance_version": EVIDENCE_PROVENANCE_VERSION,
                "evidence_scope_document_id": None,
                "evidence_scope_metadata_filter": {},
                "evidence_scope_as_of": as_of.isoformat() if as_of else None,
                "evidence_scope_snapshot_origin": "user_literal" if as_of else None,
            }
        ]
    }
    payload = TurnResolutionInput(
        current_message_id=uuid.uuid4(),
        current_message=question,
        history=history,
        request_filters=RequestFilters(as_of=as_of) if as_of is None else RequestFilters(),
        reference_time=datetime(2026, 8, 1, tzinfo=UTC),
    )
    return payload, citations, assistant_id


def test_preflight_accepts_clear_presentation_without_resolver():
    payload, citations, _assistant = _preflight_payload(
        "Translate the previous answer. Do not add new facts."
    )
    resolved = try_presentation_preflight(payload, citations_by_message=citations)
    assert resolved is not None
    assert resolved.resolution.followup_mode is FollowupMode.PRESENTATION_ONLY
    assert resolved.diagnostics["routing_origin"] == "deterministic"
    assert resolved.attempted is False


@pytest.mark.parametrize(
    "question",
    [
        "উপরের উত্তরটি নতুন কোনো তথ্য যোগ না করে ৩টি বুলেট পয়েন্টে লিখুন।",
        "আগের উত্তরের তথ্য অপরিবর্তিত রেখে এক বাক্যে লিখুন।",
    ],
)
def test_preflight_accepts_observed_bangla_transformations(question):
    payload, citations, _assistant = _preflight_payload(question)
    resolved = try_presentation_preflight(payload, citations_by_message=citations)
    assert resolved is not None
    assert resolved.resolution.followup_mode is FollowupMode.PRESENTATION_ONLY
    assert resolved.diagnostics["routing_origin"] == "deterministic"


def test_preflight_rejects_unknown_and_added_fact_wording():
    unknown, citations, _ = _preflight_payload("What about filing penalties?")
    added, _, _ = _preflight_payload("Rewrite it and add current filing fees?")
    current_fees, _, _ = _preflight_payload(
        "Summarize it, do not add anything, but what are the current fees?"
    )
    still_apply, _, _ = _preflight_payload(
        "Summarize it. Do not add new facts, but do these rules still apply?"
    )
    bangla_fees, _, _ = _preflight_payload("এটি সংক্ষেপ করুন, নতুন তথ্য যোগ করবেন না, কিন্তু বর্তমান ফি কত?")
    assert try_presentation_preflight(unknown, citations_by_message=citations) is None
    assert try_presentation_preflight(added, citations_by_message=citations) is None
    assert try_presentation_preflight(current_fees, citations_by_message=citations) is None
    assert try_presentation_preflight(still_apply, citations_by_message=citations) is None
    assert try_presentation_preflight(bangla_fees, citations_by_message=citations) is None


@pytest.mark.parametrize(
    "question",
    ["Summarize penalties", "Rewrite it for minors", "Summarize it for partnerships"],
)
def test_preflight_routes_single_token_fact_or_population_changes_to_resolver(question):
    payload, citations, _ = _preflight_payload(question)
    assert try_presentation_preflight(payload, citations_by_message=citations) is None


@pytest.mark.parametrize(
    "question",
    [
        "Explain that simply.",
        "Make it three bullets.",
        "Translate the previous answer into Bangla. Keep the same facts.",
    ],
)
def test_resolver_approved_ambiguous_or_clear_transformations_remain_presentation_only(question):
    assert (
        rewrite_followup_mode(
            question,
            TurnOutcome.RESOLVED,
            TurnRelation.FOLLOW_UP,
            FollowupMode.PRESENTATION_ONLY,
        )
        is FollowupMode.PRESENTATION_ONLY
    )


def test_preflight_keeps_inherited_historical_scope_and_exits_changed_scope():
    as_of = datetime(2025, 6, 1, tzinfo=UTC)
    payload, citations, _ = _preflight_payload("Make it shorter.", as_of=as_of)
    resolved = try_presentation_preflight(payload, citations_by_message=citations)
    assert resolved is not None
    assert resolved.retrieval.as_of == as_of
    assert resolved.retrieval.suppress_web is True
    changed = payload.model_copy(
        update={"request_filters": RequestFilters(document_id=uuid.uuid4())}
    )
    still_presentation = try_presentation_preflight(changed, citations_by_message=citations)
    assert still_presentation is not None
    saved = {
        "document_id": None,
        "metadata_filter": {},
        "as_of": as_of.isoformat(),
        "snapshot_origin": "user_literal",
    }
    assert request_scope_conflicts(changed.request_filters, saved) is True


def test_identical_as_of_zulu_and_offset_are_not_scope_conflicts():
    as_of = datetime(2025, 6, 1, tzinfo=UTC)
    saved = {
        "document_id": None,
        "metadata_filter": {},
        "as_of": "2025-06-01T00:00:00Z",
        "snapshot_origin": "user_literal",
    }
    request = RequestFilters(as_of=as_of)
    assert request_scope_conflicts(request, saved) is False
    saved["as_of"] = "2025-06-01T00:00:00+00:00"
    assert request_scope_conflicts(request, saved) is False
    saved["as_of"] = "2025-07-01T00:00:00Z"
    assert request_scope_conflicts(request, saved) is True


def test_request_scope_stringifies_metadata_filter_values():
    saved = {
        "document_id": None,
        "metadata_filter": {"year": "2024"},
        "as_of": None,
        "snapshot_origin": "user_literal",
    }
    request = RequestFilters.model_construct(metadata_filter={"year": 2024})
    assert request_scope_conflicts(request, saved) is False


def test_preflight_still_routes_mixed_web_transformations():
    payload, citations, assistant_id = _preflight_payload("Make it shorter.")
    citations[assistant_id] = [
        {"chunk_id": str(uuid.uuid4()), "source_kind": "knowledge"},
        {"chunk_id": str(uuid.uuid4()), "source_kind": "web"},
    ]
    payload = payload.model_copy(
        update={
            "history": [
                payload.history[0],
                HistoryMessage(
                    id=assistant_id,
                    role="assistant",
                    content="Indexed fact [1] and a web note [2].",
                ),
            ]
        }
    )
    assert used_citations_include_web(payload.history[-1].content, citations[assistant_id])
    mixed = try_presentation_preflight(payload, citations_by_message=citations)
    assert mixed is not None
    assert mixed.resolution.followup_mode is FollowupMode.PRESENTATION_ONLY
    assert mixed.diagnostics["routing_origin"] == "deterministic"


@pytest.mark.parametrize(
    "question",
    [
        "Summarize the rules for non-residents instead",
        "Rewrite this for the 2027 tax year",
        "Make a table of corporate filing penalties",
        "Shorten that and explain the appeal deadline",
        "Summarize it. Keep the original duties, deadlines, and applicable limitations.",
        "অনিবাসীদের জন্য সংক্ষেপ করুন",
        "২০২৭ কর বছরের জন্য এটি লিখুন",
        "এটি সংক্ষেপ করুন। মূল দায়িত্ব, সময়সীমা এবং প্রযোজ্য সীমাবদ্ধতা রাখুন।",
    ],
)
def test_preflight_rejects_residual_factual_presentation_wording(question):
    payload, citations, _ = _preflight_payload(question)
    assert rewrite_followup_mode(question, TurnOutcome.RESOLVED, TurnRelation.FOLLOW_UP) is (
        FollowupMode.NOT_APPLICABLE
    )
    assert try_presentation_preflight(payload, citations_by_message=citations) is None


def test_preflight_inherits_document_and_metadata_scope():
    payload, citations, assistant_id = _preflight_payload("Make it shorter.")
    document_id = uuid.uuid4()
    citations[assistant_id][0]["evidence_scope_document_id"] = str(document_id)
    citations[assistant_id][0]["evidence_scope_metadata_filter"] = {"country": "BD"}
    resolved = try_presentation_preflight(payload, citations_by_message=citations)
    assert resolved is not None
    assert resolved.retrieval.document_id == document_id
    assert resolved.retrieval.metadata_filter == {"country": "BD"}
    assert resolved.retrieval.suppress_web is True
