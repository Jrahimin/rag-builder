"""Recovery must respect snapshot, scope, authority, and final context budgets."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.core.config import ChatConfig, RetrievalConfig
from app.modules.conversations.grounding_service import GroundingService
from app.modules.conversations.ports import ContextChunk, ContextRetrievalResult
from app.modules.conversations.services.evidence_coverage import CoverageVerdict
from app.modules.conversations.services.evidence_repair_service import (
    _discovery_excerpts,
    _review_evidence_key,
    _search_language_instruction,
    _source_hints,
    _source_line_records,
    _unique_authority_records,
    _validated_completion,
    repair_knowledge_evidence,
)
from app.modules.conversations.turn_resolution import EffectiveRetrievalInputs
from app.platform.domain.content_hash import content_hash
from app.platform.providers.contracts.llm import ChatCompletionResult, ChatUsage
from app.platform.providers.errors import ProviderError, ProviderTimeoutError

pytestmark = pytest.mark.unit


def _coverage_review_payloads(calls):
    payloads = []
    for call in calls:
        messages = call.args[0] if call.args else []
        if not messages:
            continue
        content = getattr(messages[0], "content", "")
        if "Check whether supplied evidence" in str(content):
            payloads.append(json.loads(messages[1].content))
    return payloads


def test_review_identity_preserves_text_authority_and_requirements_but_not_rank():
    source = chunk("Exact rule", source_revision_id="revision-1")
    requirements = [{"requirement_id": "rule", "description": "Applicable rule"}]
    records = [
        {
            "outcome": "expanded",
            "base_revision_id": "revision-1",
            "target_provisions": ["62"],
        }
    ]
    key = _review_evidence_key([source], records, requirements)
    assert key == _review_evidence_key([replace(source, score=0.1)], records * 2, requirements)
    assert key == _review_evidence_key(
        [source], [*records, {"outcome": "unresolved"}], requirements
    )
    for changed in (
        replace(source, content="Changed rule"),
        replace(source, metadata={**source.metadata, "source_revision_id": "revision-2"}),
        replace(source, metadata={**source.metadata, "source_effective_from": "2027-07-01"}),
    ):
        assert key != _review_evidence_key([changed], records, requirements)
    assert key != _review_evidence_key(
        [source],
        [{"outcome": "unresolved", "base_revision_id": "revision-1"}],
        requirements,
    )
    assert key != _review_evidence_key(
        [source], records, [*requirements, {"requirement_id": "other"}]
    )
    assert key != _review_evidence_key([source], records, requirements, unresolved_ids=["other"])


@pytest.mark.parametrize("changed", [False, True])
async def test_missing_check_identity_does_not_guess_a_focused_requirement(changed):
    source = chunk("Governing salary rule.")
    next_source = replace(source, content="Different governing salary rule.") if changed else source
    calls = []
    result, retrieval, _ = await run_repair(
        [([source], {}), ([next_source], {})],
        queries=["salary"],
        requirements=[{"requirement_id": "salary", "description": "Salary rule"}],
        coverage={"complete": False, "missing": ["Applicability"], "checks": []},
        followup_queries=[{"query": "employment", "requirement_ids": ["salary"]}],
        calls=calls,
    )
    assert result.decision is None and result.partial_answer is None
    assert retrieval.retrieve.await_count == 1
    assert result.diagnostics["unbound_focused_queries_skipped"] == [
        {"query": "employment", "supplied_requirement_ids": ["salary"]}
    ]
    assert result.diagnostics["requirement_progress"]["stop_reason"] == ("no_new_focused_query")


@pytest.mark.parametrize("scope_ids", [["salary"], ["interest"], ["absent"], ["salary", "salary"]])
async def test_partial_scope_requires_every_selected_rule_and_retains_incomplete_coverage(
    scope_ids,
):
    source = chunk("The salary exclusion is one third, subject to the stated cap.")
    verdict = {
        "complete": False,
        "missing": ["Interest inclusion and final tax treatment"],
        "checks": [
            {
                "requirement_id": "salary",
                "description": "Salary exclusion",
                "supported": True,
                "evidence": [{"chunk_id": str(source.chunk_id), "quote": source.content}],
            },
            {"requirement_id": "interest", "supported": False, "evidence": []},
        ],
        "partial_answer": {
            "scope": "Explain salary exclusion and an unproved rebate formula",
            "requirement_ids": scope_ids,
            "exclusions": ["Supplied interest and combined tax liability"],
        },
    }
    result, retrieval, _ = await run_repair(
        [([source], {})],
        queries=["salary exclusion"],
        coverage=verdict,
        requirements=[
            {"requirement_id": key, "description": key} for key in ("salary", "interest")
        ],
    )
    if scope_ids == ["salary"]:
        assert result.decision.sufficient
        assert result.diagnostics["status"] == "partial_answer"
        assert result.diagnostics["coverage"]["complete"] is False
        assert result.diagnostics["coverage"]["quotes_validated"] is False
        assert result.partial_answer["pending"] == verdict["missing"]
        assert result.partial_answer["scope"] == [
            {"requirement_id": "salary", "description": "Salary exclusion"}
        ]
        assert [item.chunk_id for item in result.selected] == [source.chunk_id]
    else:
        assert result.decision is None
        assert result.partial_answer is None
    assert retrieval.retrieve.await_count == 1


async def test_missing_rule_gets_one_focused_retry_before_partial_acceptance():
    known = chunk("Private companies must hold an annual general meeting.")
    later = chunk("The annual list of members is filed with the Registrar.")
    coverage = {
        "complete": False,
        "missing": ["Annual return filing duty"],
        "checks": [
            {
                "requirement_id": "R1",
                "description": "Annual return filing duty",
                "supported": False,
                "evidence": [],
            },
            {
                "requirement_id": "R2",
                "description": "AGM duty",
                "supported": True,
                "evidence": [{"chunk_id": str(known.chunk_id), "quote": known.content}],
            },
        ],
        "partial_answer": {
            "scope": "AGM duty",
            "requirement_ids": ["R2"],
            "exclusions": ["Annual return filing duty"],
        },
    }
    result, retrieval, _ = await run_repair(
        [([known], {}), ([later], {})],
        queries=["company AGM"],
        requirements=[
            {"requirement_id": "R1", "description": "Annual return filing duty"},
            {"requirement_id": "R2", "description": "AGM duty"},
        ],
        coverage=coverage,
        followup_queries=["annual list summary"],
        final_coverage=coverage,
    )
    assert result.diagnostics["status"] == "partial_answer"
    assert result.diagnostics["focused_queries"] == ["annual list summary"]
    assert "R1" in result.diagnostics["focused_requirement_ids"]
    assert result.diagnostics["coverage"]["partial_scope_validated"] is True
    assert result.diagnostics["coverage"]["full_coverage_validated"] is False
    assert [item.chunk_id for item in result.selected] == [known.chunk_id]
    assert retrieval.retrieve.await_count == 2


async def test_supported_fragment_still_fetches_structural_continuation_before_partial():
    continuation = chunk("Continuation without governing heading.")
    predecessor = chunk("Section 36 opening applicability and return contents.")
    coverage = {
        "complete": False,
        "missing": ["filing deadline"],
        "checks": [
            {
                "requirement_id": "R1",
                "description": "Section 36 continuation",
                "supported": True,
                "needs_adjacent_context": True,
                "evidence": [
                    {"chunk_id": str(continuation.chunk_id), "quote": continuation.content}
                ],
            },
            {
                "requirement_id": "R2",
                "description": "Known AGM duty",
                "supported": True,
                "evidence": [
                    {"chunk_id": str(continuation.chunk_id), "quote": continuation.content}
                ],
            },
        ],
        "partial_answer": {
            "scope": "Known AGM duty",
            "requirement_ids": ["R2"],
            "exclusions": ["filing deadline"],
        },
    }
    result, retrieval, inputs = await run_repair(
        [([continuation], {}), ([predecessor], {})],
        queries=["annual general meeting"],
        requirements=[
            {"requirement_id": "R1", "description": "Section 36 continuation"},
            {"requirement_id": "R2", "description": "Known AGM duty"},
        ],
        coverage=coverage,
        final_coverage={
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "requirement_id": "R1",
                    "description": "Section 36 continuation",
                    "supported": True,
                    "evidence": [
                        {"chunk_id": str(predecessor.chunk_id), "quote": predecessor.content}
                    ],
                },
                {
                    "requirement_id": "R2",
                    "description": "Known AGM duty",
                    "supported": True,
                    "evidence": [
                        {"chunk_id": str(continuation.chunk_id), "quote": continuation.content}
                    ],
                },
            ],
        },
        adjacent=True,
    )
    assert retrieval.retrieve.await_count == 2
    assert retrieval.retrieve.call_args_list[1].kwargs["adjacent_to"] == [continuation.chunk_id]
    assert result.diagnostics["status"] == "recovered"
    assert retrieval.retrieve.call_args_list[1].kwargs["document_id"] == inputs.document_id
    assert {item.chunk_id for item in result.selected} == {
        continuation.chunk_id,
        predecessor.chunk_id,
    }


async def test_focused_anchor_can_trigger_structural_recovery_in_final_round():
    known = chunk("Private companies must file an annual list.")
    anchor = chunk("Section 36 filing rule continues in the following paragraph.")
    neighbor = chunk("The annual list must be filed within twenty-one days.")

    def proof(requirement_id, description, source, *, supported=True):
        return {
            "requirement_id": requirement_id,
            "description": description,
            "supported": supported,
            "evidence": [{"chunk_id": str(source.chunk_id), "quote": source.content}],
        }

    initial_coverage = {
        "complete": False,
        "missing": ["Annual return deadline"],
        "checks": [
            {
                "requirement_id": "R1",
                "description": "Annual return deadline",
                "supported": False,
                "evidence": [],
            },
            proof("R2", "Annual return duty", known),
        ],
        "partial_answer": {
            "scope": "Annual return duty",
            "requirement_ids": ["R2"],
            "exclusions": ["Annual return deadline"],
        },
    }
    anchor_coverage = {
        **initial_coverage,
        "checks": [
            proof("R1", "Annual return deadline", anchor, supported=False),
            proof("R2", "Annual return duty", known),
        ],
    }
    complete = {
        "complete": True,
        "missing": [],
        "checks": [
            {
                "requirement_id": "R1",
                "description": "Annual return deadline",
                "supported": True,
                "evidence": [
                    {"chunk_id": str(anchor.chunk_id), "quote": anchor.content},
                    {"chunk_id": str(neighbor.chunk_id), "quote": neighbor.content},
                ],
            },
            proof("R2", "Annual return duty", known),
        ],
    }
    result, retrieval, _ = await run_repair(
        [([known], {}), ([anchor], {}), ([neighbor], {})],
        queries=[{"query": "annual return duty", "requirement_ids": ["R2"]}],
        requirements=[
            {
                "requirement_id": "R1",
                "description": "Annual return deadline",
                "origin": "explicit_user_request",
            },
            {
                "requirement_id": "R2",
                "description": "Annual return duty",
                "origin": "explicit_user_request",
            },
        ],
        coverage=initial_coverage,
        followup_queries=[{"query": "annual return deadline", "requirement_ids": ["R1"]}],
        final_coverage=anchor_coverage,
        second_final_coverage=complete,
        late_adjacent=True,
        user_query="What are the annual return duty and deadline?",
    )
    assert result.diagnostics["status"] == "recovered"
    assert {item.chunk_id for item in result.selected} == {
        known.chunk_id,
        anchor.chunk_id,
        neighbor.chunk_id,
    }
    assert result.diagnostics["focused_requirement_ids"] == ["R1"]
    assert [attempt["route"] for attempt in result.diagnostics["requirement_attempts"]] == [
        "search",
        "focused",
        "adjacent",
    ]
    assert retrieval.retrieve.call_args_list[-1].kwargs["adjacent_to"] == [anchor.chunk_id]


async def test_duplicate_focused_query_stops_and_keeps_confirmed_partial_proof():
    known = chunk("The governing rate is 10%.")
    coverage = {
        "complete": False,
        "missing": ["exclusion"],
        "checks": [
            {
                "requirement_id": "rate",
                "description": "Rate",
                "supported": True,
                "evidence": [{"chunk_id": str(known.chunk_id), "quote": known.content}],
            },
            {"requirement_id": "exclusion", "supported": False, "evidence": []},
        ],
        "partial_answer": {
            "scope": "Rate",
            "requirement_ids": ["rate"],
            "exclusions": ["exclusion"],
        },
    }
    result, retrieval, _ = await run_repair(
        [([known], {})],
        queries=["rate"],
        requirements=[
            {"requirement_id": "rate", "description": "Rate"},
            {"requirement_id": "exclusion", "description": "Exclusion"},
        ],
        coverage=coverage,
        followup_queries=["rate"],
    )
    assert result.diagnostics["status"] == "partial_answer"
    assert result.diagnostics.get("duplicate_focused_queries_skipped") == 1
    assert result.diagnostics["requirement_progress"]["stop_reason"] == "no_new_focused_query"
    assert [item.chunk_id for item in result.selected] == [known.chunk_id]
    assert retrieval.retrieve.await_count == 1


async def test_unchanged_focused_evidence_keeps_confirmed_partial_proof():
    known = chunk("Private companies must hold an annual general meeting.")
    coverage = {
        "complete": False,
        "missing": ["Annual return filing duty"],
        "checks": [
            {
                "requirement_id": "R1",
                "description": "Annual return filing duty",
                "supported": False,
                "evidence": [],
            },
            {
                "requirement_id": "R2",
                "description": "AGM duty",
                "supported": True,
                "evidence": [{"chunk_id": str(known.chunk_id), "quote": known.content}],
            },
        ],
        "partial_answer": {
            "scope": "AGM duty",
            "requirement_ids": ["R2"],
            "exclusions": ["Annual return filing duty"],
        },
    }
    result, retrieval, _ = await run_repair(
        [([known], {}), ([known], {})],
        queries=["company AGM"],
        requirements=[
            {"requirement_id": "R1", "description": "Annual return filing duty"},
            {"requirement_id": "R2", "description": "AGM duty"},
        ],
        coverage=coverage,
        followup_queries=["annual list summary"],
        final_coverage=coverage,
    )
    assert result.diagnostics["status"] == "partial_answer"
    assert result.diagnostics["requirement_progress"]["stop_reason"] == (
        "unchanged_review_evidence"
    )
    assert result.diagnostics["requirement_progress"]["focused_requirement_ids"] == ["R1"]
    assert result.partial_answer is not None
    assert [item.chunk_id for item in result.selected] == [known.chunk_id]
    assert retrieval.retrieve.await_count == 2


@pytest.mark.parametrize(
    "stage",
    [
        "focused_planning",
        "coverage_review",
        "deadline",
        "authority_changed",
        "proof_changed",
        "snapshot_changed",
    ],
)
async def test_timeout_keeps_only_previously_validated_partial_proof(stage):
    known = chunk(
        "Private companies must hold an annual general meeting.",
        source_revision_id="agm-revision",
    )
    unreviewed = chunk("An unreviewed annual filing rule.")
    coverage = {
        "complete": False,
        "missing": ["Annual return filing duty"],
        "checks": [
            {"requirement_id": "R1", "supported": False, "evidence": []},
            {
                "requirement_id": "R2",
                "description": "AGM duty",
                "supported": True,
                "evidence": [{"chunk_id": str(known.chunk_id), "quote": known.content}],
            },
        ],
        "partial_answer": {
            "scope": "AGM duty",
            "requirement_ids": ["R2"],
            "exclusions": ["Annual return filing duty"],
        },
    }

    async def wait_for_deadline(messages):
        if "Find governing evidence missed" in messages[0].content:
            await asyncio.Event().wait()

    branch_metadata = {}
    if stage == "authority_changed":
        branch_metadata["modifies_expansion_records"] = [
            {
                "outcome": "ungoverned_or_incomplete_metadata",
                "base_revision_id": "agm-revision",
                "target_provisions": [],
            }
        ]
    elif stage == "snapshot_changed":
        branch_metadata["source_metadata_generation"] = 25
    if stage == "proof_changed":
        unreviewed = replace(known, content="A changed rule which has not been reviewed.")
    followup_chunks = [unreviewed]
    if stage == "proof_changed":
        followup_chunks.append(chunk("Additional unreviewed filing context."))

    result, _, _ = await run_repair(
        [([known], {}), (followup_chunks, branch_metadata)],
        queries=["company AGM"],
        requirements=[
            {"requirement_id": "R1", "description": "Annual return filing duty"},
            {"requirement_id": "R2", "description": "AGM duty"},
        ],
        coverage=coverage,
        followup_queries=["annual list summary"],
        followup_error=TimeoutError() if stage == "focused_planning" else None,
        final_verification_error=(
            ProviderTimeoutError("Review timed out", provider_name="fake")
            if stage in {"coverage_review", "authority_changed", "proof_changed"}
            else None
        ),
        generation_hook=wait_for_deadline if stage == "deadline" else None,
        timeout_seconds=0.3 if stage == "deadline" else 300,
    )
    if stage in {"authority_changed", "proof_changed", "snapshot_changed"}:
        assert not result.selected
        assert result.decision is None
        assert result.partial_answer is None
        return
    assert result.failure is None
    assert result.diagnostics["status"] == "partial_answer"
    assert result.diagnostics["requirement_progress"]["stop_reason"] == "evidence_review_timeout"
    assert result.diagnostics["coverage"]["partial_scope_validated"] is True
    assert result.diagnostics["coverage"]["full_coverage_validated"] is False
    assert [(c.chunk_id, c.content) for c in result.selected] == [(known.chunk_id, known.content)]
    assert result.partial_answer["pending"] == ["Annual return filing duty"]
    assert result.usage == ChatUsage(None, None)
    assert result.diagnostics["elapsed_ms"] >= 0


async def test_overall_review_deadline_cannot_promote_unreviewed_evidence():
    async def block_initial_review(messages):
        if "Check whether supplied evidence" in messages[0].content:
            await asyncio.Event().wait()

    result, _, _ = await run_repair(
        [([chunk("An admitted rule, not yet reviewed for coverage.")], {})],
        queries=["rule"],
        generation_hook=block_initial_review,
        timeout_seconds=0.3,
    )
    assert not result.selected
    assert result.decision is None
    assert isinstance(result.failure, ProviderTimeoutError)
    assert result.diagnostics["phase"] == "coverage_review"
    assert result.diagnostics["failure_reason"] == "timeout"
    assert result.diagnostics["stop_reason"] == "evidence_review_timeout"
    assert result.diagnostics["timeout_seconds"] == 0.3


@pytest.mark.parametrize("final_finish", ["stop", "length"])
async def test_truncated_structured_request_restarts_once_with_bounded_budget(final_finish):
    from app.platform.providers.contracts.llm import ChatMessage, ChatRole

    truncated = ChatCompletionResult(
        content='{"complete":tru',
        provider="fake",
        model="test",
        provider_version="1",
        finish_reason="length",
        usage=ChatUsage(10, 1024),
    )
    retried = replace(
        truncated,
        content='{"complete":false,"missing":["rule"],"checks":[]}',
        finish_reason=final_finish,
        usage=ChatUsage(10, 1500),
    )
    llm = AsyncMock()
    llm.generate.side_effect = [truncated, retried]
    messages = [ChatMessage(ChatRole.USER, "Review the evidence")]
    result = await _validated_completion(
        llm,
        messages,
        schema=CoverageVerdict,
        max_tokens=1024,
        truncation_retry_tokens=2048,
    )
    assert llm.generate.await_count == 2
    assert [call.kwargs["max_tokens"] for call in llm.generate.call_args_list] == [1024, 2048]
    assert all(call.args[0] == messages for call in llm.generate.call_args_list)
    assert result.content == retried.content
    assert result.finish_reason == final_finish
    assert result.usage == ChatUsage(20, 2524)


def test_structured_source_selectors_preserve_original_line_positions():
    assert _source_line_records("Title\n\nEvidence\n\nThe year is 1998.") == [
        {"start_line": 1, "end_line": 1, "text": "Title"},
        {"start_line": 3, "end_line": 3, "text": "Evidence"},
        {"start_line": 5, "end_line": 5, "text": "The year is 1998."},
    ]


@pytest.mark.parametrize("retry_line", [2, 3])
async def test_blank_source_selector_gets_one_structural_correction_without_acceptance(retry_line):
    from pydantic import ValidationError

    from app.modules.conversations.services.evidence_coverage import numbered_source_lines
    from app.platform.providers.contracts.llm import ChatMessage, ChatRole

    source = chunk("Catalogue\n\nThe premiere year is 1998.")

    def completion(line):
        return ChatCompletionResult(
            content=json.dumps(
                {
                    "complete": True,
                    "missing": [],
                    "checks": [
                        {
                            "requirement_id": "year",
                            "supported": True,
                            "evidence": [{"chunk_id": "E1", "start_line": line, "end_line": line}],
                        }
                    ],
                }
            ),
            provider="fake",
            model="test",
            finish_reason="stop",
            usage=ChatUsage(1, 1),
            provider_version="1",
        )

    def replacement_completion(line):
        return ChatCompletionResult(
            content=json.dumps(
                {
                    "replacements": [
                        {
                            "requirement_id": "year",
                            "query_index": -1,
                            "evidence_index": 0,
                            "chunk_id": "E1",
                            "start_line": line,
                            "end_line": line,
                        }
                    ]
                }
            ),
            provider="fake",
            model="test",
            finish_reason="stop",
            usage=ChatUsage(1, 1),
            provider_version="1",
        )

    llm = AsyncMock()
    llm.generate.side_effect = [completion(2), replacement_completion(retry_line)]
    payload = {
        "original_question": "Select the evidence line",
        "as_of": "2026-09-14",
        "context": [{"chunk_id": "E1", "content": numbered_source_lines(source.content)}],
    }

    async def validate():
        return await _validated_completion(
            llm,
            [ChatMessage(ChatRole.USER, json.dumps(payload))],
            schema=CoverageVerdict,
            max_tokens=1024,
            proof_context=[source],
            source_ids={"E1": str(source.chunk_id)},
        )

    if retry_line == 2:
        with pytest.raises(ValidationError):
            await validate()
    else:
        result = await validate()
        assert json.loads(result.content)["checks"][0]["evidence"][0]["start_line"] == 3
    assert llm.generate.await_count == 2
    assert json.loads(llm.generate.call_args_list[0].args[0][0].content) == payload
    retried = json.loads(llm.generate.call_args_list[1].args[0][0].content)
    assert retried == {
        **payload,
        "context": [{"chunk_id": "E1", "source_lines": _source_line_records(source.content)}],
    }
    issues = json.loads(
        llm.generate.call_args_list[1].args[0][-1].content.split(" Failed selectors: ")[1]
    )
    metadata = issues[0]
    assert metadata["source_id"] in {"E1", str(source.chunk_id)}
    assert metadata["source_known"] is True
    assert metadata["line_count"] == 3
    assert metadata["nonempty_lines"] == [1, 3]


@pytest.mark.parametrize("case", ["unknown_id", "different_text", "plain_message", "no_context"])
def test_structured_selector_retry_cannot_rewrite_unmatched_evidence(case):
    from app.modules.conversations.services.evidence_coverage import numbered_source_lines
    from app.modules.conversations.services.evidence_repair_service import (
        _structured_selector_retry,
    )
    from app.platform.providers.contracts.llm import ChatMessage, ChatRole

    source = chunk("Exact source text.\n\nIts condition.")
    payload = {"context": [{"chunk_id": "E1", "content": numbered_source_lines(source.content)}]}
    if case == "unknown_id":
        payload["context"][0]["chunk_id"] = "E99"
    if case == "different_text":
        payload["context"][0]["content"] = "Different text"
    messages = [
        ChatMessage(ChatRole.USER, "Question" if case == "plain_message" else json.dumps(payload))
    ]
    assert (
        _structured_selector_retry(
            messages, None if case == "no_context" else [source], {"E1": str(source.chunk_id)}
        )
        == messages
    )


@pytest.mark.parametrize("compact_labels", [False, True])
async def test_complete_initial_requirement_proof_skips_all_retrieval(compact_labels):
    config = ChatConfig()
    source = chunk("The film premiered in 1998.")
    grounding = GroundingService(config)
    decision = grounding.assess("When did the film premiere?", [source], rerank_status="off")
    selected = list(decision.admitted_units) or [source]
    proof = {
        "queries": [],
        "requirements": [{"requirement_id": "premiere", "description": "Premiere year"}],
        "coverage": {
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "requirement_id": "premiere",
                    "supported": True,
                    "evidence": [
                        {
                            "chunk_id": "E1" if compact_labels else str(selected[0].chunk_id),
                            "start_line": 1,
                            "end_line": 1,
                        }
                    ],
                }
            ],
        },
    }
    llm, retrieval = AsyncMock(), AsyncMock()
    llm.generate.return_value = ChatCompletionResult(
        content=json.dumps(proof),
        provider="fake",
        model="test",
        provider_version="1",
        finish_reason="stop",
        usage=ChatUsage(1, 1),
    )
    result = await repair_knowledge_evidence(
        inputs=EffectiveRetrievalInputs(query="When did the film premiere?"),
        initial=ContextRetrievalResult(
            [source], {"index_build_id": "build", "source_metadata_generation": 1}
        ),
        selected=selected,
        initial_decision=decision,
        retrieval=retrieval,
        llm=llm,
        grounding=grounding,
        chat_config=config,
        retrieval_config=RetrievalConfig(),
        max_output_tokens=4096,
        evidence_approach="factual",
    )
    assert result.diagnostics["status"] == "initial_evidence_complete"
    assert result.decision.sufficient
    assert len(result.selected) == 1
    retrieval.retrieve.assert_not_awaited()
    retrieval.retrieve_batch.assert_not_awaited()
    assert llm.generate.await_count == 1


def test_authoritative_discovery_preserves_source_payload_without_work_group_noise():
    source = chunk(
        "source", source_role="primary", source_work_key="reprint", source_group_id="group"
    )
    legacy = _source_hints([source])[0]["source"]
    comparative = _source_hints([source], include_work_metadata=True)[0]["source"]
    assert legacy["source_role"] == "primary"
    assert "source_work_key" not in legacy and "source_group_id" not in legacy
    assert comparative["source_work_key"] == "reprint"
    assert comparative["source_group_id"] == "group"


def test_source_hints_retain_languages_after_repeated_top_source():
    first = chunk("first", language="en")
    other = chunk("other", language="bn")
    hints = _source_hints([first] * 12 + [other])
    assert [hint["source"]["language"] for hint in hints] == ["en", "bn"]


def test_concept_language_prefers_current_governing_source_over_old_edition():
    hints = _source_hints(
        [
            chunk("old", language="en", source_role="primary", source_effective_from="2023-01-01"),
            chunk(
                "current", language="bn", source_role="primary", source_effective_from="2026-07-01"
            ),
        ]
    )
    assert "language code bn" in _search_language_instruction(hints)


def test_discovery_prioritizes_reviewed_gaps_without_starving_other_topics():
    noise = [chunk(f"Unrelated procedural passage {i}.") for i in range(8)]
    example = chunk("Example referring to the governing employment exclusion.")
    rebate = chunk("Investment eligibility requirements.")
    verdict = CoverageVerdict.model_validate(
        {
            "complete": False,
            "missing": ["employment exclusion", "investment eligibility"],
            "checks": [
                {
                    "query_index": 0,
                    "supported": False,
                    "evidence": [{"chunk_id": str(example.chunk_id), "quote": example.content}],
                },
                {"query_index": 1, "supported": False, "evidence": []},
            ],
        }
    )
    excerpts = _discovery_excerpts(
        verdict, [[*noise, example], [rebate]], [*noise, example, rebate]
    )
    assert [item["content"] for item in excerpts[:2]] == [example.content, rebate.content]
    assert len(excerpts) == 8


def chunk(text: str, **metadata: object) -> ContextChunk:
    return ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=1,
        content=text,
        score=0.95,
        semantic_score=0.95,
        filename="current guide.pdf",
        chunk_hash=content_hash(text),
        metadata=metadata,
    )


@pytest.mark.parametrize("initial_unresolved", [False, True])
@pytest.mark.parametrize("same_chunk", [False, True])
async def test_authoritative_recovery_retains_only_safe_initial_proof(
    initial_unresolved, same_chunk
):
    initial = chunk("The inspection period is 12 months.")
    if initial_unresolved:
        initial = replace(initial, metadata={**initial.metadata, "authority_status": "unresolved"})
    discovered = chunk("The renewal fee is stated separately.")
    if same_chunk:
        discovered = replace(discovered, chunk_id=initial.chunk_id)
    calls = []
    result, retrieval, _ = await run_repair(
        [([discovered], {})],
        queries=["inspection period"],
        requirements=[{"requirement_id": "period", "description": "Inspection period"}],
        selected_context=[initial],
        coverage={
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "requirement_id": "period",
                    "supported": True,
                    "evidence": [{"chunk_id": str(initial.chunk_id), "quote": initial.content}],
                }
            ],
        },
        calls=calls,
    )
    reviewed = json.loads(calls[1].args[0][1].content)["context"]
    assert any(initial.content in c.get("content", "") for c in reviewed) is not initial_unresolved
    assert result.diagnostics["retained_initial_chunk_ids"] == (
        [] if initial_unresolved else [str(initial.chunk_id)]
    )
    assert bool(result.selected) is not initial_unresolved
    if result.selected:
        assert [c.chunk_id for c in result.selected] == [initial.chunk_id]
    assert retrieval.retrieve.await_count == 1


@pytest.mark.parametrize("output_cap", [2048, 4096, 6144, 8192, 16384])
async def test_authoritative_planner_truncation_uses_larger_bounded_retry(output_cap):
    source = chunk("Refunds are allowed within 30 days.")
    calls = []
    result, retrieval, _ = await run_repair(
        [([source], {})],
        queries=["refund deadline"],
        calls=calls,
        planning_truncated=True,
        max_output_tokens=output_cap,
    )
    assert calls[0].kwargs["max_tokens"] == min(4096, output_cap)
    if output_cap <= 4096:
        assert len(calls) == 1
        assert result.diagnostics["status"] == "incomplete_plan"
        assert result.diagnostics["planning_finish_reason"] == "length"
        assert not result.selected
        assert retrieval.retrieve.await_count == 0
    else:
        assert calls[1].kwargs["max_tokens"] == min(8192, output_cap)
        assert result.diagnostics["planning_finish_reason"] == "stop"
        assert result.diagnostics["status"] == "recovered"
        assert retrieval.retrieve.await_count == 1
        assert len(calls) == 3  # truncated plan, complete plan, source coverage


@pytest.mark.parametrize("valid_proof", [False, True])
async def test_corrected_planner_reference_keeps_identity_and_requires_source_proof(valid_proof):
    source = chunk("Section 7: Annual renewal is due within 30 days.")
    result, _, _ = await run_repair(
        [([source], {})],
        queries=["annual renewal deadline"],
        user_query="What is the annual renewal deadline?",
        requirements=[{"requirement_id": "renewal", "description": "Section 3 renewal deadline"}],
        coverage={
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "requirement_id": "renewal",
                    "description": "Section 7 annual renewal deadline",
                    "supported": True,
                    "evidence": [
                        {
                            "chunk_id": str(source.chunk_id),
                            "quote": source.content if valid_proof else "Renewal is optional.",
                        }
                    ],
                }
            ],
        },
    )
    assert bool(result.selected) is valid_proof
    if valid_proof:
        assert result.diagnostics["status"] == "recovered"
        assert result.partial_answer is None


async def run_repair(
    branches,
    *,
    queries=None,
    config=None,
    initial_records=None,
    coverage=None,
    verification_finish="stop",
    verification_error=None,
    followup_error=None,
    final_verification_error=None,
    generation_hook=None,
    timeout_seconds=300,
    calls=None,
    selected_context=None,
    followup_queries=None,
    final_coverage=None,
    second_followup_queries=None,
    second_final_coverage=None,
    adjacent=False,
    late_adjacent=False,
    requirements=None,
    input_gap_kinds=None,
    input_gap_error=None,
    planning_truncated=False,
    max_output_tokens=1024,
    user_query="Calculate from gross salary and eligible investment for this period.",
    evidence_approach="authoritative",
    initial_decision=None,
    plan_coverage=None,
):
    config = config or ChatConfig()
    queries = (
        queries
        if queries is not None
        else ["gross salary exemption", "general rate bands", "current investment rebate"]
    )
    plan_queries = queries
    if requirements:
        all_requirement_ids = [item["requirement_id"] for item in requirements]
        plan_queries = [
            item
            if isinstance(item, dict)
            else {"query": item, "requirement_ids": all_requirement_ids}
            for item in queries
        ]
    llm = AsyncMock()
    plan = ChatCompletionResult(
        content=json.dumps(
            {
                "queries": plan_queries,
                "requirements": requirements or [],
                **({"coverage": plan_coverage} if plan_coverage is not None else {}),
            }
        ),
        provider="fake",
        model="test",
        finish_reason="stop",
        usage=ChatUsage(12, 8),
        provider_version="1",
    )
    verdict = (
        coverage
        if coverage is not None
        else {
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "query_index": i,
                    "supported": True,
                    "evidence": [{"chunk_id": str(items[0].chunk_id), "quote": items[0].content}],
                }
                for i, (items, _) in enumerate(branches)
                if items
            ],
        }
    )

    def bound_followups(items, current_verdict):
        if not requirements or not items:
            return items or []
        missing_ids = [
            check.get("requirement_id")
            for check in current_verdict.get("checks", [])
            if not check.get("supported") and check.get("requirement_id")
        ]
        return [
            item
            if isinstance(item, dict)
            else {
                "query": item,
                "requirement_ids": missing_ids[:1],
            }
            for item in items
        ]

    llm.generate.side_effect = [
        *(
            [replace(plan, content="", finish_reason="length", usage=ChatUsage(10, 2048))]
            if planning_truncated
            else []
        ),
        plan,
        verification_error
        or replace(
            plan,
            content=json.dumps(verdict),
            finish_reason=verification_finish,
        ),
    ]
    llm.generate.side_effect = [
        *llm.generate.side_effect,
        followup_error
        or replace(
            plan,
            content=json.dumps({"queries": bound_followups(followup_queries, verdict)}),
            usage=ChatUsage(0, 0),
        ),
        final_verification_error or replace(plan, content=json.dumps(final_coverage or verdict)),
        replace(
            plan,
            content=json.dumps(
                {"queries": bound_followups(second_followup_queries, final_coverage or verdict)}
            ),
        ),
        replace(plan, content=json.dumps(second_final_coverage or final_coverage or verdict)),
    ]
    retrieval = AsyncMock()
    if adjacent or late_adjacent:
        retrieval.supports_adjacent_retrieval = True
    if adjacent:
        completions = list(llm.generate.side_effect)
        llm.generate.side_effect = [*completions[:2], *completions[3:]]
    elif late_adjacent:
        completions = list(llm.generate.side_effect)
        llm.generate.side_effect = [*completions[:4], completions[5]]
    scripted_responses = iter(llm.generate.side_effect)

    async def respond(messages, **kwargs):
        if generation_hook is not None:
            await generation_hook(messages)
        if "Classify unresolved requirements" in messages[0].content:
            if input_gap_error is not None:
                raise input_gap_error
            payload = json.loads(messages[1].content)
            return replace(
                plan,
                usage=ChatUsage(0, 0),
                content=json.dumps(
                    {
                        "gaps": [
                            {
                                "gap_index": gap["gap_index"],
                                "kind": input_gap_kinds[i] if input_gap_kinds else "source_rule",
                            }
                            for i, gap in enumerate(payload["gaps"])
                        ]
                    }
                ),
            )
        response = next(scripted_responses)
        if isinstance(response, Exception):
            raise response
        return response

    llm.generate.side_effect = respond
    snapshot = {"index_build_id": "build-a", "source_metadata_generation": 24}
    retrieval.retrieve.side_effect = [
        ContextRetrievalResult(chunks=items, diagnostics={**snapshot, **diagnostics})
        for items, diagnostics in branches
    ]
    inputs = EffectiveRetrievalInputs(
        query=user_query,
        document_id=uuid.uuid4(),
        metadata_filter={"region": "local"},
        as_of=datetime(2026, 7, 1, tzinfo=UTC),
        suppress_web=True,
    )
    result = await repair_knowledge_evidence(
        inputs=inputs,
        initial=ContextRetrievalResult(
            chunks=[],
            diagnostics={
                **snapshot,
                "modifies_expansion_records": initial_records or [],
            },
        ),
        selected=selected_context or [],
        retrieval=retrieval,
        llm=llm,
        grounding=GroundingService(config),
        chat_config=config,
        retrieval_config=RetrievalConfig(),
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout_seconds,
        evidence_approach=evidence_approach,
        initial_decision=initial_decision,
    )
    if calls is not None:
        calls.extend(llm.generate.call_args_list)
    return result, retrieval, inputs


async def test_four_dependencies_preserve_source_authority_in_coverage_input():
    calls = []
    sources = [
        replace(
            chunk(
                f"Governing rule for dependency {i}.",
                source_role="supporting",
                source_type="official_guidance",
                source_effective_from="2026-07-01",
            ),
            page_number=20,
        )
        for i in range(4)
    ]
    result, retrieval, _ = await run_repair(
        [([source], {}) for source in sources],
        queries=[f"dependency {i}" for i in range(4)],
        calls=calls,
        selected_context=[
            chunk("Obsolete proposed forty-percent exemption", source_role="reference")
        ],
    )
    assert result.diagnostics["status"] == "recovered"
    assert retrieval.retrieve.await_count == 4
    planning_input = json.loads(calls[0].args[0][1].content)
    assert "Obsolete proposed forty-percent exemption" not in json.dumps(planning_input)
    assert planning_input["source_hints"][0]["source"]["source_role"] == "reference"
    coverage_input = json.loads(calls[1].args[0][1].content)
    assert all(
        item["source"]["source_role"] == "supporting"
        and item["source"]["source_type"] == "official_guidance"
        and item["source"]["source_effective_from"] == "2026-07-01"
        and item["page_number"] == 20
        and item["chunk_index"] == 1
        for item in coverage_input["context"]
    )


async def test_coverage_cannot_self_classify_a_source_gap_as_personal():
    source = chunk("The ordinary rate is ten percent.")
    result, _, _ = await run_repair(
        [([source], {})],
        queries=["ordinary rate"],
        coverage={
            "complete": True,
            "missing": [],
            "missing_inputs": ["The amended exemption rule is not established."],
            "checks": [
                {
                    "query_index": 0,
                    "supported": True,
                    "evidence": [{"chunk_id": str(source.chunk_id), "quote": source.content}],
                }
            ],
        },
    )
    assert result.decision is None
    assert result.missing_inputs == ()


async def test_recovers_separate_salary_band_and_rebate_dependencies_with_original_scope():
    texts = [
        "Employment income exemption: one third of gross salary, capped at 500000.",
        "General rate bands for 2026: first 400000 nil, next 300000 ten percent.",
        "Current investment rebate is the lower of three percent of income, "
        "ten percent of investment and 750000.",
    ]
    result, retrieval, inputs = await run_repair([([chunk(text)], {}) for text in texts])
    assert result.diagnostics["status"] == "recovered"
    assert [item.content for item in result.selected] == texts
    assert result.decision is not None and result.decision.sufficient
    assert result.usage == ChatUsage(24, 16)
    for call in retrieval.retrieve.call_args_list:
        assert call.kwargs["document_id"] == inputs.document_id
        assert call.kwargs["metadata_filter"] == inputs.metadata_filter
        assert call.kwargs["as_of"] == inputs.as_of


async def test_coverage_review_serializes_uuid_authority_diagnostics_for_the_llm():
    authority_record = {
        "base_revision_id": uuid.uuid4(),
        "modifier_revision_id": uuid.uuid4(),
        "outcome": "ungoverned_or_incomplete_metadata",
        "target_provisions": [],
    }
    result, retrieval, _ = await run_repair(
        [([chunk("Refunds are available within 45 days.")], {})],
        queries=["refund terms"],
        initial_records=[authority_record],
    )
    assert result.diagnostics["status"] == "recovered"
    # The service would have raised before invoking the review provider if UUIDs
    # were not converted to JSON-safe identity strings.
    assert retrieval.retrieve.await_count == 1


@pytest.mark.parametrize(
    "change", [{"index_build_id": "build-b"}, {"source_metadata_generation": 25}]
)
async def test_does_not_mix_snapshots_during_repair(change):
    result, _, _ = await run_repair([([chunk("Gross salary exemption")], change)])
    assert result.diagnostics["status"] == "snapshot_changed"
    assert result.decision is None and not result.selected


async def test_old_rule_cannot_become_trusted_when_a_branch_omits_its_relationship():
    old = chunk("Current investment rebate is fifteen percent.", source_revision_id="old")
    result, _, _ = await run_repair(
        [([old], {})],
        queries=["current investment rebate"],
        initial_records=[
            {
                "base_revision_id": "old",
                "modifier_revision_id": "new",
                "outcome": "ungoverned_or_incomplete_metadata",
                "target_provisions": [],
            }
        ],
    )
    assert result.diagnostics["status"] == "coverage_incomplete"
    assert not result.selected


async def test_orphan_table_never_repairs_a_dependency_despite_high_similarity():
    table = chunk("Rate bands: first 450000 nil", element_type="table")
    result, _, _ = await run_repair([([table], {})], queries=["general rate bands"])
    assert result.diagnostics["status"] == "coverage_incomplete"


async def test_later_branch_cannot_leave_an_earlier_unknown_rule_in_final_context():
    earlier = chunk("General rate bands", source_revision_id="old")
    result, _, _ = await run_repair(
        [
            ([earlier], {}),
            (
                [chunk("Current investment rebate")],
                {
                    "modifies_expansion_records": [
                        {
                            "base_revision_id": "old",
                            "modifier_revision_id": "new",
                            "outcome": "ungoverned_or_incomplete_metadata",
                            "target_provisions": [],
                        }
                    ]
                },
            ),
        ],
        queries=["general rate bands", "current investment rebate"],
    )
    assert result.diagnostics["status"] == "dependency_unresolved"
    assert result.decision is None and not result.selected


async def test_final_budget_must_retain_every_dependency():
    result, _, _ = await run_repair(
        [
            ([chunk(text)], {})
            for text in [
                "gross salary exemption",
                "general rate bands",
                "current investment rebate",
            ]
        ],
        config=ChatConfig(max_context_chunks=2),
    )
    assert result.diagnostics["status"] == "dependency_exceeds_budget"
    assert not result.selected


async def test_broad_recovery_reserves_budget_for_focused_dependencies():
    focused = [chunk(f"Governing duty {i} applies.") for i in range(4)]
    initial = [chunk(f"Broad introductory context {i}.") for i in range(4)]
    requirements = [{"requirement_id": f"R{i}", "description": f"Duty {i}"} for i in range(4)]
    result, _, _ = await run_repair(
        [([source], {}) for source in focused],
        queries=[f"duty {i}" for i in range(4)],
        requirements=requirements,
        selected_context=initial,
        config=ChatConfig(max_context_chunks=4),
        coverage={
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "requirement_id": f"R{i}",
                    "supported": True,
                    "evidence": [{"chunk_id": str(source.chunk_id), "quote": source.content}],
                }
                for i, source in enumerate(focused)
            ],
        },
    )
    assert result.diagnostics["status"] == "recovered"
    assert {c.chunk_id for c in result.selected} == {c.chunk_id for c in focused}


async def test_narrowed_partial_duty_keeps_unresolved_details_explicit():
    source = chunk("Directors must present audited accounts at the annual meeting.")
    result, _, _ = await run_repair(
        [([source], {})],
        queries=["accounts"],
        requirements=[{"requirement_id": "R1", "description": "Accounts and auditor appointment"}],
        coverage={
            "complete": False,
            "missing": ["Auditor appointment"],
            "checks": [
                {
                    "requirement_id": "R1",
                    "description": "Present audited accounts at the AGM",
                    "supported": True,
                    "evidence": [{"chunk_id": str(source.chunk_id), "quote": source.content}],
                }
            ],
            "partial_answer": {
                "scope": "Accounts presentation only",
                "requirement_ids": ["R1"],
                "exclusions": ["Auditor appointment"],
            },
        },
        input_gap_kinds=["source_rule"],
    )
    assert result.diagnostics["status"] == "partial_answer"
    assert result.partial_answer["scope"][0]["description"] == "Present audited accounts at the AGM"
    assert result.partial_answer["pending"] == ["Auditor appointment"]
    assert result.diagnostics["requirement_progress"]["stop_reason"] == "validated_partial_scope"


@pytest.mark.parametrize("queries", [[], [" "], ["q"] * 9, ["q" * 501]])
async def test_invalid_plans_never_search_or_authorize_generation(queries):
    result, retrieval, _ = await run_repair([], queries=queries)
    assert result.decision is None
    retrieval.retrieve.assert_not_called()


async def test_non_tax_policy_recovery_uses_the_same_path():
    result, _, _ = await run_repair(
        [([chunk("Enterprise renewal charges are waived for eligible customers.")], {})],
        queries=["Enterprise renewal eligibility and charges"],
    )
    assert result.diagnostics["status"] == "recovered"


async def test_relevant_definitions_and_unlinked_old_translation_do_not_prove_coverage():
    branches = [
        ([chunk("Employment income means salary and benefits.")], {}),
        ([chunk("File returns with the applicable city corporation office.")], {}),
        (
            [
                chunk(
                    "Section 78: fifteen percent of investment, capped at one million.",
                    source_revision_id="english-act",
                )
            ],
            {},
        ),
    ]
    result, _, _ = await run_repair(
        branches,
        initial_records=[
            {
                "base_revision_id": "bangla-act",
                "modifier_revision_id": "new",
                "outcome": "ungoverned_or_incomplete_metadata",
                "target_provisions": [],
            }
        ],
        coverage={
            "complete": False,
            "missing": ["Applicable period, exemption and amended rebate"],
            "checks": [{"query_index": i, "supported": False, "evidence": []} for i in range(3)],
        },
    )
    assert result.diagnostics["status"] == "coverage_incomplete"
    assert result.decision is None and not result.selected
    assert result.usage == ChatUsage(24, 16)


@pytest.mark.parametrize("missing_inputs", [[], ["Date of purchase"]])
@pytest.mark.parametrize("fault", ["invented_quote", "foreign_id", "missing_check", "missing_rule"])
async def test_verifier_cannot_authorize_unbound_or_incomplete_evidence(fault, missing_inputs):
    source = chunk("Eligible refunds must be requested within 45 days.")
    verdict = {
        "complete": True,
        "missing": [],
        "missing_inputs": missing_inputs,
        "checks": [
            {
                "query_index": 0,
                "supported": True,
                "evidence": [{"chunk_id": str(source.chunk_id), "quote": source.content}],
            }
        ],
    }
    if fault == "invented_quote":
        verdict["checks"][0]["evidence"][0]["quote"] = "Refunds are unlimited."
    elif fault == "foreign_id":
        verdict["checks"][0]["evidence"][0]["chunk_id"] = str(uuid.uuid4())
    elif fault == "missing_check":
        verdict["checks"] = []
    else:
        verdict["missing"] = ["Customer eligibility"]
    result, _, _ = await run_repair([([source], {})], queries=["refund terms"], coverage=verdict)
    assert result.diagnostics["status"] == (
        "repair_unavailable"
        if missing_inputs or fault in {"missing_check", "missing_rule"}
        else "coverage_incomplete"
    )
    assert result.decision is None and not result.selected
    assert not result.missing_inputs


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"missing_inputs": ["Private date"]}, "coverage_unreviewed_missing_inputs"),
        ({"complete": True}, "coverage_inconsistent_completion"),
    ],
)
async def test_coverage_failure_codes_survive_bounded_validation_without_exposing_input(
    fields, code
):
    from pydantic import ValidationError

    invalid = {"complete": False, "missing": ["Missing rule"], "checks": [], **fields}
    llm = AsyncMock()
    llm.generate.return_value = ChatCompletionResult(
        content=json.dumps(invalid),
        provider="fake",
        model="test",
        provider_version="1",
        finish_reason="stop",
        usage=ChatUsage(1, 1),
    )
    with pytest.raises(ValidationError) as caught:
        await _validated_completion(llm, [], schema=CoverageVerdict, max_tokens=1024)
    assert llm.generate.await_count == 2
    errors = caught.value.errors(include_input=False, include_url=False)
    assert [{"type": e["type"], "loc": list(e["loc"])} for e in errors] == [
        {"type": code, "loc": []}
    ]
    assert "Private date" not in json.dumps(errors)


async def test_misaligned_advisory_gap_labels_keep_gaps_without_retrying_model():
    llm = AsyncMock()
    payload = {
        "complete": False,
        "missing": ["Filing duty", "Deadline"],
        "gap_kinds": ["scenario_input"],
        "checks": [],
    }
    llm.generate.return_value = ChatCompletionResult(
        content=json.dumps(payload),
        provider="fake",
        model="test",
        provider_version="1",
        finish_reason="stop",
        usage=ChatUsage(10, 5),
    )
    response = await _validated_completion(llm, [], schema=CoverageVerdict, max_tokens=1024)
    verdict = CoverageVerdict.model_validate_json(response.content)
    assert llm.generate.await_count == 1
    assert verdict.missing == payload["missing"]
    assert verdict.gap_kinds == ["source_rule", "source_rule"]
    assert verdict._gap_kinds_defaulted
    assert not verdict.missing_inputs
    assert not verdict.validates([], [])


def test_authority_prompt_dedup_keeps_distinct_outcomes_and_scopes():
    base = {"relationship_id": "edge", "outcome": "expanded", "target_provisions": ["36"]}
    changed = {**base, "outcome": "candidate_cap"}
    scope = {**base, "target_provisions": ["81"]}
    assert _unique_authority_records(
        [base, dict(reversed(list(base.items()))), changed, scope]
    ) == [base, changed, scope]
    instruction = _search_language_instruction(
        [{"source": {"language": "bn", "source_role": "primary"}}]
    )
    assert "one short source-language query per distinct" in instruction
    assert "in each language for every" not in instruction


async def test_proven_conditional_rules_do_not_search_for_missing_personal_facts():
    source = chunk("A refund is available if the purchase was within 45 days.")
    verdict = {
        "complete": False,
        "missing": ["Date of purchase"],
        "checks": [
            {
                "query_index": 0,
                "supported": True,
                "evidence": [{"chunk_id": str(source.chunk_id), "start_line": 1, "end_line": 1}],
            }
        ],
    }
    calls = []
    result, retrieval, _ = await run_repair(
        [([source], {})],
        queries=["refund eligibility"],
        coverage=verdict,
        calls=calls,
        input_gap_kinds=["scenario_input"],
    )
    assert result.decision.sufficient
    assert result.missing_inputs == ("Date of purchase",)
    assert result.diagnostics["coverage"]["missing_inputs"] == ["Date of purchase"]
    assert result.diagnostics["coverage"]["quotes_validated"] is True
    assert len(calls) == 3  # Plan, coverage, input classification; no extra corpus searches.
    assert retrieval.retrieve.await_count == 1


async def test_failed_input_gap_review_cannot_authorize_answer_or_claim_known_usage():
    source = chunk("A refund is available within 45 days of purchase.")
    result, _, _ = await run_repair(
        [([source], {})],
        queries=["refund eligibility"],
        coverage={
            "complete": False,
            "missing": ["Date of purchase"],
            "checks": [
                {
                    "query_index": 0,
                    "supported": True,
                    "evidence": [{"chunk_id": str(source.chunk_id), "quote": source.content}],
                }
            ],
        },
        input_gap_error=ProviderError("unavailable", provider_name="fake"),
    )
    assert result.decision is None
    assert result.missing_inputs == ()
    assert result.usage == ChatUsage(None, None)


@pytest.mark.parametrize(
    "kinds",
    [
        ["source_rule", "scenario_input"],
        ["scenario_input", "source_rule"],
    ],
)
async def test_positive_checks_do_not_hide_a_rule_gap_in_the_original_question(kinds):
    source = chunk("Refunds are available within 45 days.")
    result, _, _ = await run_repair(
        [([source], {})],
        queries=["refund eligibility"],
        coverage={
            "complete": False,
            "missing": ["Unresolved condition", "Unresolved amount"],
            "checks": [
                {
                    "query_index": 0,
                    "supported": True,
                    "evidence": [{"chunk_id": str(source.chunk_id), "quote": source.content}],
                }
            ],
        },
        input_gap_kinds=kinds,
    )
    assert result.decision is None
    assert result.missing_inputs == ()
    assert result.diagnostics["input_gap_reviews"][0]["only_inputs"] is False


@pytest.mark.parametrize("indices", [[0], [0, 0], [0, 2], [0, 1, 2]])
def test_input_classification_cannot_drop_duplicate_or_add_gaps(indices):
    from app.modules.conversations.services.evidence_coverage import InputGapReview

    review = InputGapReview.model_validate(
        {
            "gaps": [{"gap_index": i, "kind": "scenario_input"} for i in indices],
        }
    )
    assert not review.only_inputs_for(2)


async def test_missing_rule_and_personal_fact_still_block_generation():
    source = chunk("A refund may depend on the purchase date.")
    result, _, _ = await run_repair(
        [([source], {})],
        queries=["refund eligibility"],
        coverage={
            "complete": False,
            "missing": ["Governing refund time limit", "Date of purchase"],
            "checks": [{"query_index": 0, "supported": False, "evidence": []}],
        },
    )
    assert result.decision is None
    assert result.missing_inputs == ()
    assert result.diagnostics["coverage"]["missing"] == [
        "Governing refund time limit",
        "Date of purchase",
    ]


async def test_truncated_positive_verdict_cannot_enable_generation():
    result, _, _ = await run_repair(
        [([chunk("Refunds are available within 45 days.")], {})],
        queries=["refund terms"],
        verification_finish="length",
    )
    assert result.diagnostics["status"] == "coverage_incomplete"
    assert result.decision is None and not result.selected


async def test_final_admitted_evidence_can_support_another_search_dependency():
    terms = chunk("Refunds require a receipt and are limited to 45 days.")
    receipt = chunk("Receipt records identify the purchase.")
    verdict = {
        "complete": True,
        "missing": [],
        "checks": [
            {
                "query_index": i,
                "supported": True,
                "evidence": [{"chunk_id": str(terms.chunk_id), "quote": terms.content}],
            }
            for i in range(2)
        ],
    }
    result, _, _ = await run_repair(
        [([terms], {}), ([receipt], {})],
        queries=["refund time limit", "required receipt"],
        coverage=verdict,
    )
    assert result.diagnostics["status"] == "recovered"
    assert [c.chunk_id for c in result.selected] == [terms.chunk_id]
    assert result.diagnostics["proof_chunk_ids"] == [str(terms.chunk_id)]


async def test_rule_below_four_relevant_candidates_survives_for_completeness_review():
    sources = [chunk(f"Refund policy background item {i}.") for i in range(4)]
    rule = chunk("The governing refund period is 45 days.")
    verdict = {
        "complete": True,
        "missing": [],
        "checks": [
            {
                "query_index": 0,
                "supported": True,
                "evidence": [{"chunk_id": str(rule.chunk_id), "quote": rule.content}],
            }
        ],
    }
    result, _, _ = await run_repair(
        [([*sources, rule], {})],
        queries=["refund period"],
        coverage=verdict,
    )
    assert result.diagnostics["status"] == "recovered"
    assert rule.chunk_id in {c.chunk_id for c in result.selected}
    assert len(result.selected) == 1
    assert {unit.chunk_id for unit in result.decision.admitted_units} == {rule.chunk_id}


@pytest.mark.parametrize("label", ["E1", "E99"])
async def test_short_passage_labels_bind_only_to_the_final_context(label):
    source = chunk("Refunds are available for 45 days.")
    coverage = {
        "complete": True,
        "missing": [],
        "checks": [
            {
                "query_index": 0,
                "supported": True,
                "evidence": [{"chunk_id": label, "quote": source.content}],
            }
        ],
    }
    result, _, _ = await run_repair([([source], {})], queries=["refunds"], coverage=coverage)
    assert (result.diagnostics["status"] == "recovered") is (label == "E1")
    if label == "E1":
        assert result.diagnostics["coverage"]["checks"][0]["chunk_ids"] == [str(source.chunk_id)]


@pytest.mark.parametrize(
    ("quote", "accepted"),
    [
        ("দফা (৩৬)-তে আয় বাদ। Rate: 10%.", True),
        ("দফা (৩৬)-তে আয় বাদ। Rate: 15%.", False),
        ("দফা (৩৬)-তে আয় বাদ নয়। Rate: 10%.", False),
        ("দফা (৩৬)-তে আয় বাদ। Rate: 1 0%.", False),
        ("দফা (৩৬)-তে আয় বাদ। Rate: 10.", False),
    ],
)
async def test_ocr_quote_spacing_preserves_all_words_numbers_and_punctuation(quote, accepted):
    source = chunk("দফা ( ৩৬ ) -তে\n\nআয় বাদ । Rate : 10 % .")
    coverage = {
        "complete": True,
        "missing": [],
        "checks": [
            {
                "query_index": 0,
                "supported": True,
                "evidence": [{"chunk_id": str(source.chunk_id), "quote": quote}],
            }
        ],
    }
    result, _, _ = await run_repair([([source], {})], queries=["Rate"], coverage=coverage)
    assert (result.diagnostics["status"] == "recovered") is accepted


@pytest.mark.parametrize(
    "error", [TimeoutError(), ProviderError("unavailable", provider_name="fake")]
)
async def test_verification_failure_preserves_failure_and_unknown_usage(error):
    result, _, _ = await run_repair(
        [([chunk("Refunds are available within 45 days.")], {})],
        queries=["refund terms"],
        verification_error=error,
    )
    assert result.diagnostics["status"] == "repair_unavailable"
    assert result.decision is None and not result.selected
    assert result.usage == ChatUsage(None, None)


@pytest.mark.parametrize(
    "missing", ["Employment income exclusion", "Applicable minimum tax period"]
)
async def test_focused_followup_recovers_missing_rule_without_discarding_initial_evidence(missing):
    first = chunk("Current rate bands apply to resident individuals.")
    focused = chunk("Current governing exclusion and minimum-tax conditions.")

    def check(i, source):
        return {
            "query_index": i,
            "supported": True,
            "evidence": [{"chunk_id": str(source.chunk_id), "quote": source.content}],
        }

    calls = []
    result, retrieval, inputs = await run_repair(
        [([first], {}), ([focused], {})],
        queries=["current rates"],
        coverage={"complete": False, "missing": [missing], "checks": [check(0, first)]},
        followup_queries=["focused governing rule"],
        final_coverage={
            "complete": True,
            "missing": [],
            "checks": [check(0, first), check(1, focused)],
        },
        calls=calls,
    )
    assert result.diagnostics["status"] == "recovered"
    assert {c.chunk_id for c in result.selected} == {first.chunk_id, focused.chunk_id}
    focused_call = next(
        call for call in calls if "Find governing evidence missed" in call.args[0][0].content
    )
    assert json.loads(focused_call.args[0][1].content)["missing_requirements"] == [missing]
    assert result.diagnostics["initial_coverage"]["complete"] is False
    assert retrieval.retrieve.await_count == 2
    assert retrieval.retrieve.call_args.kwargs["as_of"] == inputs.as_of
    assert result.usage == ChatUsage(36, 24)


async def test_focused_followup_rejects_snapshot_change():
    first = chunk("Current governing rates.")
    result, _, _ = await run_repair(
        [([first], {}), ([chunk("Exclusion rule")], {"index_build_id": "different"})],
        queries=["rates"],
        followup_queries=["exclusion"],
        coverage={"complete": False, "missing": ["exclusion"], "checks": []},
    )
    assert result.diagnostics["status"] == "snapshot_changed"
    assert not result.selected


async def test_focused_followup_stops_when_no_new_alternative_is_available():
    result, retrieval, _ = await run_repair(
        [([chunk("Current rates")], {}), ([chunk("Related definitions")], {})],
        queries=["rates"],
        followup_queries=["exclusion"],
        coverage={"complete": False, "missing": ["exclusion"], "checks": []},
    )
    assert result.diagnostics["status"] == "coverage_incomplete"
    assert retrieval.retrieve.await_count == 2
    assert not result.selected


async def test_focused_discovery_uses_missing_branch_excerpts_without_promoting_them():
    example = chunk("Worked example applies a one-third exclusion; see Schedule Six.")
    rule = chunk("The governing exclusion is one third, subject to the stated cap.")
    calls = []
    result, _, _ = await run_repair(
        [([example], {}), ([rule], {})],
        queries=["verbose unsuccessful employment search"],
        coverage={
            "complete": False,
            "missing": ["Governing exclusion"],
            "checks": [{"query_index": 0, "supported": False, "evidence": []}],
        },
        followup_queries=["employment exclusion schedule"],
        final_coverage={
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "query_index": i,
                    "supported": True,
                    "evidence": [{"chunk_id": str(rule.chunk_id), "quote": rule.content}],
                }
                for i in range(2)
            ],
        },
        calls=calls,
    )
    followup = json.loads(calls[2].args[0][1].content)
    assert followup["discovery_excerpts"] == [
        {"title": example.filename, "content": example.content}
    ]
    assert [c.chunk_id for c in result.selected] == [rule.chunk_id]
    assert "Worked example" not in json.dumps(result.diagnostics)


@pytest.mark.parametrize("first_response", ['```json\n{"queries":["rule"]}\n```', "not JSON"])
async def test_json_format_recovery_remains_schema_validated(first_response):
    from app.modules.conversations.services.evidence_repair_service import (
        _SearchPlan,
        _validated_completion,
    )

    base = ChatCompletionResult(first_response, "fake", "test", "stop", ChatUsage(2, 3), "1")
    llm = AsyncMock()
    llm.generate.side_effect = [base, replace(base, content='{"queries":["rule"]}')]
    response = await _validated_completion(llm, [], schema=_SearchPlan, max_tokens=1024)
    parsed = _SearchPlan.model_validate_json(response.content)
    assert [query.query for query in parsed.queries] == ["rule"]
    expected_calls = 1 if first_response.startswith("```") else 2
    assert llm.generate.await_count == expected_calls
    assert response.usage == ChatUsage(2 * expected_calls, 3 * expected_calls)


def test_search_plan_binds_queries_and_drops_optional_corroboration_work():
    from app.modules.conversations.services.evidence_repair_service import (
        _prepare_search_plan,
        _SearchPlan,
    )

    plan = _SearchPlan.model_validate(
        {
            "requirements": [
                {
                    "requirement_id": "R1",
                    "description": "Annual return deadline",
                    "origin": "explicit_user_request",
                },
                {
                    "requirement_id": "R2",
                    "description": "Separate agency confirmation",
                    "origin": "optional_corroboration",
                },
            ],
            "queries": [
                {"query": "annual return deadline", "requirement_ids": ["R1"]},
                {"query": "agency confirmation", "requirement_ids": ["R2"]},
            ],
        }
    )
    requirements, queries, ownership = _prepare_search_plan(plan)
    assert [item.requirement_id for item in requirements] == ["R1"]
    assert queries == ["annual return deadline"]
    assert ownership == {"annual return deadline": ["R1"]}


def test_search_plan_keeps_mixed_ownership_until_every_owned_requirement_is_proven():
    from app.modules.conversations.services.evidence_repair_service import (
        _prepare_search_plan,
        _SearchPlan,
    )

    plan = _SearchPlan.model_validate(
        {
            "requirements": [
                {"requirement_id": "R1", "description": "Filing duty"},
                {"requirement_id": "R2", "description": "AGM duty"},
            ],
            "queries": [
                {"query": "filing duty", "requirement_ids": ["R1"]},
                {"query": "agm duty", "requirement_ids": ["R2"]},
                {"query": "company duties", "requirement_ids": ["R1", "R2"]},
            ],
        }
    )
    _, queries, ownership = _prepare_search_plan(plan, proven_ids={"R2"})
    assert queries == ["filing duty", "company duties"]
    assert ownership["company duties"] == ["R1", "R2"]
    _, remaining, _ = _prepare_search_plan(plan, proven_ids={"R1", "R2"})
    assert remaining == []


@pytest.mark.parametrize(
    ("question", "description"),
    [
        (
            "What are the consequences of non-compliance?",
            "Consequences including penalties, late fees and prosecution",
        ),
        (
            "না মানলে কী পরিণতি হবে?",
            "পরিণতি, জরিমানা, বিলম্ব ফি ও মামলার ঝুঁকি",
        ),
    ],
)
def test_explicit_consequences_are_never_removed_by_optional_detail_words(question, description):
    from app.modules.conversations.services.evidence_repair_service import (
        _prepare_search_plan,
        _SearchPlan,
    )

    plan = _SearchPlan.model_validate(
        {
            "requirements": [
                {
                    "requirement_id": "consequences",
                    "description": description,
                    "origin": "explicit_user_request",
                }
            ],
            "queries": [
                {"query": "non-compliance consequences", "requirement_ids": ["consequences"]}
            ],
        }
    )
    requirements, queries, ownership = _prepare_search_plan(plan, question)
    assert [item.requirement_id for item in requirements] == ["consequences"]
    assert queries == ["non-compliance consequences"]
    assert ownership[queries[0]] == ["consequences"]


def test_search_plan_does_not_override_required_origin_using_description_words():
    from app.modules.conversations.services.evidence_repair_service import (
        _prepare_search_plan,
        _SearchPlan,
    )

    plan = _SearchPlan.model_validate(
        {
            "requirements": [
                {
                    "requirement_id": "R1",
                    "description": "Annual return duty and deadline",
                    "origin": "explicit_user_request",
                },
                {
                    "requirement_id": "R2",
                    "description": "Separate official guidance confirmation",
                    "origin": "necessary_applicability",
                },
            ],
            "queries": [
                {"query": "annual return deadline", "requirement_ids": ["R1"]},
                {"query": "official confirmation", "requirement_ids": ["R2"]},
            ],
        }
    )
    requirements, queries, ownership = _prepare_search_plan(
        plan, "What is the annual return duty and deadline?"
    )
    assert [item.requirement_id for item in requirements] == ["R1", "R2"]
    assert queries == ["annual return deadline", "official confirmation"]
    assert ownership["annual return deadline"] == ["R1"]
    assert ownership["official confirmation"] == ["R2"]


def test_legacy_unbound_query_is_not_assigned_to_a_requirement_by_position():
    from app.modules.conversations.services.evidence_repair_service import (
        _prepare_search_plan,
        _SearchPlan,
    )

    plan = _SearchPlan.model_validate(
        {
            "requirements": [
                {"requirement_id": "R1", "description": "RJSC annual return duty"},
                {"requirement_id": "R2", "description": "Income tax return duty"},
            ],
            "queries": ["section 170 tax return"],
        }
    )
    requirements, queries, ownership = _prepare_search_plan(plan)
    assert [item.requirement_id for item in requirements] == ["R1", "R2"]
    assert queries == []
    assert ownership == {}


async def test_invalid_focused_requirement_id_cannot_consume_an_unrelated_retry():
    source = chunk("Private companies must hold an annual general meeting.")
    result, retrieval, _ = await run_repair(
        [([source], {}), ([chunk("Income tax return rule")], {})],
        queries=[{"query": "company AGM", "requirement_ids": ["R2"]}],
        requirements=[
            {"requirement_id": "R1", "description": "Annual return filing duty"},
            {"requirement_id": "R2", "description": "AGM duty"},
        ],
        coverage={
            "complete": False,
            "missing": ["Annual return filing duty"],
            "checks": [
                {"requirement_id": "R1", "supported": False, "evidence": []},
                {
                    "requirement_id": "R2",
                    "supported": True,
                    "evidence": [{"chunk_id": str(source.chunk_id), "quote": source.content}],
                },
            ],
        },
        followup_queries=[{"query": "section 170 tax return", "requirement_ids": ["R2"]}],
    )
    assert retrieval.retrieve.await_count == 1
    assert result.diagnostics["unbound_focused_queries_skipped"] == [
        {"query": "section 170 tax return", "supplied_requirement_ids": ["R2"]}
    ]
    assert "R1" not in result.diagnostics.get("focused_requirement_ids", [])


async def test_empty_search_route_can_be_proved_by_another_route():
    rule = chunk("The governing employment exclusion applies to ordinary employees.")
    verdict = {
        "complete": True,
        "missing": [],
        "checks": [
            {
                "query_index": i,
                "supported": True,
                "evidence": [{"chunk_id": str(rule.chunk_id), "start_line": 1, "end_line": 1}],
            }
            for i in range(2)
        ],
    }
    result, retrieval, _ = await run_repair(
        [([], {}), ([rule], {})],
        queries=["unsuccessful wording", "governing wording"],
        coverage=verdict,
    )
    assert result.diagnostics["status"] == "recovered"
    assert retrieval.retrieve.await_count == 2
    assert [c.chunk_id for c in result.selected] == [rule.chunk_id]


async def test_second_focused_pass_is_bounded_and_can_resolve_remaining_gap():
    example = chunk("Worked example")
    rule = chunk("Complete governing rule")
    incomplete = {"complete": False, "missing": ["governing rule"], "checks": []}
    result, retrieval, _ = await run_repair(
        [([example], {}), ([], {}), ([rule], {})],
        queries=["initial search"],
        coverage=incomplete,
        followup_queries=["first alternative"],
        final_coverage=incomplete,
        second_followup_queries=["second alternative"],
        second_final_coverage={
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "query_index": i,
                    "supported": True,
                    "evidence": [{"chunk_id": str(rule.chunk_id), "start_line": 1, "end_line": 1}],
                }
                for i in range(3)
            ],
        },
    )
    assert result.diagnostics["status"] == "recovered"
    assert retrieval.retrieve.await_count == 3
    assert result.diagnostics["focused_queries"] == ["first alternative", "second alternative"]


@pytest.mark.parametrize("start,end", [(0, 1), (2, 1), (1, 99)])
async def test_invalid_source_ranges_cannot_authorize_answer(start, end):
    rule = chunk("A rule with punctuation: no invented dash.\nThe rate is 10%.")
    result, _, _ = await run_repair(
        [([rule], {})],
        queries=["rate"],
        coverage={
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "query_index": 0,
                    "supported": True,
                    "evidence": [
                        {"chunk_id": str(rule.chunk_id), "start_line": start, "end_line": end}
                    ],
                }
            ],
        },
    )
    assert not result.selected


async def test_source_range_materialization_preserves_punctuation_and_numbers():
    rule = chunk("Scope: ordinary employees.\n\nThe rate is 10%, not 15%.")
    calls = []
    result, _, _ = await run_repair(
        [([rule], {})],
        queries=["rate"],
        calls=calls,
        coverage={
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "query_index": 0,
                    "supported": True,
                    "evidence": [{"chunk_id": str(rule.chunk_id), "start_line": 1, "end_line": 3}],
                }
            ],
        },
    )
    assert result.diagnostics["status"] == "recovered"
    assert result.selected[0].content == rule.content
    assert json.loads(calls[1].args[0][1].content)["context"][0]["content"].startswith("L1: Scope:")


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("anchor_supported", [False, True])
async def test_adjacent_recovery_keeps_scope_and_rechecks_original_requirements(
    partial, anchor_supported
):
    continuation = chunk("Continuation without governing heading.")
    governing = chunk("The applicable rate is 10% for the current period.")
    calls = []
    result, retrieval, inputs = await run_repair(
        [([continuation], {}), ([governing], {})],
        queries=["Discovery wording mentioning unrelated future years"],
        requirements=[{"requirement_id": key, "description": key} for key in ("missing", "known")]
        if partial
        else None,
        coverage={
            "complete": False,
            "missing": ["separate unrelated missing topic", "governing heading"],
            "checks": [
                {
                    "query_index": 0,
                    "supported": anchor_supported,
                    "needs_adjacent_context": True,
                    "description": "Governing heading and scope for this continuation",
                    **({"requirement_id": "missing"} if partial else {}),
                    "evidence": [
                        {"chunk_id": str(continuation.chunk_id), "start_line": 1, "end_line": 1}
                    ],
                },
                *(
                    [
                        {
                            "requirement_id": "known",
                            "supported": True,
                            "evidence": [
                                {
                                    "chunk_id": str(continuation.chunk_id),
                                    "start_line": 1,
                                    "end_line": 1,
                                }
                            ],
                        }
                    ]
                    if partial
                    else []
                ),
            ],
            **(
                {
                    "partial_answer": {
                        "scope": "Known scope",
                        "requirement_ids": ["known"],
                        "exclusions": ["governing heading"],
                    }
                }
                if partial
                else {}
            ),
        },
        final_coverage={
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "query_index": i,
                    **({"requirement_id": ("missing", "known")[i]} if partial else {}),
                    "supported": True,
                    "evidence": [
                        {"chunk_id": str(governing.chunk_id), "start_line": 1, "end_line": 1}
                    ],
                }
                for i in range(2)
            ],
        },
        adjacent=True,
        calls=calls,
    )
    assert result.diagnostics["status"] == "recovered"
    # All-supported/incomplete reviews also classify the unresolved input gap.
    # Neither path needs another search-planning call for deterministic neighbours.
    assert len(calls) == 3 + int(anchor_supported)
    request = retrieval.retrieve.call_args_list[1].kwargs
    assert request["adjacent_to"] == [continuation.chunk_id]
    assert request["query"] == "Governing heading and scope for this continuation"
    assert request["document_id"] == inputs.document_id
    assert request["metadata_filter"] == inputs.metadata_filter
    assert request["as_of"] == inputs.as_of
    review = json.loads(calls[1].args[0][1].content)
    assert "unrelated future years" not in json.dumps(review)
    assert review["original_question"] == inputs.query


async def test_contradictory_complete_verdict_gets_one_format_retry():
    from app.modules.conversations.services.evidence_repair_service import _validated_completion

    contradictory = {
        "complete": True,
        "missing": [],
        "checks": [{"query_index": 0, "supported": False, "evidence": []}],
    }
    corrected = {
        "complete": True,
        "missing": [],
        "checks": [
            {
                "query_index": 0,
                "supported": True,
                "evidence": [{"chunk_id": "E1", "start_line": 1, "end_line": 2}],
            }
        ],
    }
    llm = AsyncMock()
    llm.generate.side_effect = [
        ChatCompletionResult(
            content=json.dumps(value),
            provider="fake",
            provider_version="1",
            model="test",
            finish_reason="stop",
            usage=ChatUsage(1, 1),
        )
        for value in (contradictory, corrected)
    ]
    result = await _validated_completion(llm, [], schema=CoverageVerdict, max_tokens=1024)
    assert json.loads(result.content) == corrected
    assert llm.generate.await_count == 2
    assert "every check" in llm.generate.call_args.args[0][-1].content
    # The correction still needs exact source validation; JSON consistency alone
    # cannot authorize generation from invented line ranges.
    assert not CoverageVerdict.model_validate_json(result.content).resolve_source_ranges([])


async def test_followup_carries_confirmed_proof_without_old_search_noise():
    known = chunk("The governing rate is 10%.")
    example = chunk("An illustrative example, not the missing governing exclusion.")
    noise = chunk("Unrelated administrative procedure.")
    exclusion = chunk("The governing exclusion applies to ordinary employees.")
    calls = []
    initial_check = {
        "query_index": 0,
        "supported": True,
        "evidence": [{"chunk_id": str(known.chunk_id), "quote": known.content}],
    }
    result, _, _ = await run_repair(
        [([known, noise], {}), ([example], {}), ([exclusion], {})],
        queries=["rate", "employment exclusion"],
        config=ChatConfig(max_context_chunks=3),
        coverage={
            "complete": False,
            "missing": ["governing exclusion"],
            "checks": [initial_check, {"query_index": 1, "supported": False, "evidence": []}],
        },
        followup_queries=["alternative exclusion search"],
        calls=calls,
        final_coverage={
            "complete": True,
            "missing": [],
            "checks": [
                initial_check,
                *[
                    {
                        "query_index": i,
                        "supported": True,
                        "evidence": [
                            {"chunk_id": str(exclusion.chunk_id), "quote": exclusion.content}
                        ],
                    }
                    for i in (1, 2)
                ],
            ],
        },
    )
    assert result.diagnostics["status"] == "recovered"
    context = json.loads(calls[3].args[0][1].content)["context"]
    assert {item["content"] for item in context} == {
        "L1: " + known.content,
        "L1: " + exclusion.content,
    }


async def test_semantic_confirmed_proof_has_priority_over_new_search_noise():
    calls = []
    known = chunk("The governing rate is 10%.")
    missing = chunk("The governing exclusion applies to ordinary employees.")
    noise = chunk("Unrelated administrative procedure.")
    check = {
        "requirement_id": "rate",
        "description": "Applicable rate",
        "supported": True,
        "evidence": [{"chunk_id": str(known.chunk_id), "quote": known.content}],
    }
    result, _, _ = await run_repair(
        [([known], {}), ([missing, noise], {})],
        queries=["rate"],
        calls=calls,
        requirements=[
            {"requirement_id": "rate", "description": "Rate"},
            {"requirement_id": "exclusion", "description": "Exclusion"},
        ],
        config=ChatConfig(max_context_chunks=2),
        coverage={
            "complete": False,
            "missing": ["exclusion"],
            "checks": [check, {"requirement_id": "exclusion", "supported": False, "evidence": []}],
        },
        followup_queries=["employment exclusion"],
        final_coverage={
            "complete": True,
            "missing": [],
            "checks": [
                check,
                {
                    "requirement_id": "exclusion",
                    "supported": True,
                    "evidence": [{"chunk_id": str(missing.chunk_id), "quote": missing.content}],
                },
            ],
        },
    )
    assert result.diagnostics["status"] == "recovered"
    assert {c.chunk_id for c in result.selected} == {known.chunk_id, missing.chunk_id}
    assert json.loads(calls[2].args[0][1].content)["supported_requirements"] == [
        {"requirement_id": "rate", "description": "Applicable rate"}
    ]


@pytest.mark.parametrize("followups", [["  CURRENT   RATE  "], ["current\trate"]])
async def test_cosmetic_focused_duplicate_stops_before_search_and_review(followups):
    source = chunk("An incomplete governing provision.")
    calls = []
    result, retrieval, _ = await run_repair(
        [([source], {})],
        queries=["current rate"],
        coverage={"complete": False, "missing": ["Applicability"], "checks": []},
        followup_queries=followups,
        calls=calls,
    )
    assert retrieval.retrieve.await_count == 1
    assert len(calls) == 3
    assert result.decision is None and result.partial_answer is None
    assert result.diagnostics["duplicate_focused_queries_skipped"] == 1


async def test_authoritative_initial_complete_proof_skips_retrieval_without_extra_llm():
    config = ChatConfig()
    source = chunk("Private companies must hold an annual general meeting.")
    grounding = GroundingService(config)
    decision = grounding.assess("What is the AGM duty?", [source], rerank_status="off")
    selected = list(decision.admitted_units) or [source]
    proof = {
        "complete": True,
        "missing": [],
        "checks": [
            {
                "requirement_id": "agm",
                "description": "AGM duty",
                "supported": True,
                "evidence": [
                    {
                        "chunk_id": str(selected[0].chunk_id),
                        "start_line": 1,
                        "end_line": 1,
                    }
                ],
            }
        ],
    }
    llm, retrieval = AsyncMock(), AsyncMock()
    llm.generate.return_value = ChatCompletionResult(
        content=json.dumps(
            {
                "queries": [{"query": "annual general meeting", "requirement_ids": ["agm"]}],
                "requirements": [{"requirement_id": "agm", "description": "AGM duty"}],
                "coverage": proof,
            }
        ),
        provider="fake",
        model="test",
        provider_version="1",
        finish_reason="stop",
        usage=ChatUsage(1, 1),
    )
    result = await repair_knowledge_evidence(
        inputs=EffectiveRetrievalInputs(query="What is the AGM duty?"),
        initial=ContextRetrievalResult(
            [source], {"index_build_id": "build", "source_metadata_generation": 1}
        ),
        selected=selected,
        initial_decision=decision,
        retrieval=retrieval,
        llm=llm,
        grounding=grounding,
        chat_config=config,
        retrieval_config=RetrievalConfig(),
        max_output_tokens=4096,
        evidence_approach="authoritative",
    )
    assert result.diagnostics["status"] == "initial_evidence_complete"
    assert result.decision.sufficient
    assert result.answerable_scope["complete"] is True
    retrieval.retrieve.assert_not_awaited()
    retrieval.retrieve_batch.assert_not_awaited()
    assert llm.generate.await_count == 1


async def test_all_proven_filtered_queries_handoff_instead_of_invalid_plan():
    config = ChatConfig()
    source = chunk("Private companies must hold an annual general meeting.")
    grounding = GroundingService(config)
    decision = grounding.assess("What is the AGM duty?", [source], rerank_status="off")
    selected = list(decision.admitted_units) or [source]
    result, retrieval, _ = await run_repair(
        [],
        queries=[{"query": "annual general meeting", "requirement_ids": ["agm"]}],
        requirements=[{"requirement_id": "agm", "description": "AGM duty"}],
        selected_context=selected,
        initial_decision=decision,
        plan_coverage={
            "complete": False,
            "missing": [],
            "checks": [
                {
                    "requirement_id": "agm",
                    "description": "AGM duty",
                    "supported": True,
                    "evidence": [
                        {
                            "chunk_id": str(selected[0].chunk_id),
                            "start_line": 1,
                            "end_line": 1,
                        }
                    ],
                }
            ],
        },
        user_query="What is the AGM duty?",
    )
    assert result.diagnostics["status"] != "invalid_plan"
    assert result.diagnostics["status"] in {
        "recovered",
        "initial_evidence_complete",
        "partial_answer",
    }
    assert result.decision is not None and result.decision.sufficient
    retrieval.retrieve.assert_not_awaited()
    retrieval.retrieve_batch.assert_not_awaited()


async def test_authoritative_partial_initial_proof_filters_proven_queries():
    known = chunk("Private companies must hold an annual general meeting.")
    later = chunk("The annual list of members is filed with the Registrar.")
    config = ChatConfig()
    grounding = GroundingService(config)
    decision = grounding.assess("What are the AGM and filing duties?", [known], rerank_status="off")
    selected = list(decision.admitted_units) or [known]
    calls = []
    result, retrieval, _ = await run_repair(
        [([later], {}), ([later], {})],
        queries=[
            {"query": "annual return filing", "requirement_ids": ["R1"]},
            {"query": "company AGM", "requirement_ids": ["R2"]},
            {"query": "company duties overview", "requirement_ids": ["R1", "R2"]},
        ],
        requirements=[
            {"requirement_id": "R1", "description": "Annual return filing duty"},
            {"requirement_id": "R2", "description": "AGM duty"},
        ],
        selected_context=selected,
        initial_decision=decision,
        plan_coverage={
            "complete": False,
            "missing": ["Annual return filing duty"],
            "checks": [
                {"requirement_id": "R1", "supported": False, "evidence": []},
                {
                    "requirement_id": "R2",
                    "description": "AGM duty",
                    "supported": True,
                    "evidence": [
                        {
                            "chunk_id": str(selected[0].chunk_id),
                            "start_line": 1,
                            "end_line": 1,
                        }
                    ],
                },
            ],
        },
        coverage={
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "requirement_id": "R1",
                    "supported": True,
                    "evidence": [{"chunk_id": str(later.chunk_id), "quote": later.content}],
                },
                {
                    "requirement_id": "R2",
                    "description": "AGM duty",
                    "supported": True,
                    "evidence": [
                        {
                            "chunk_id": str(selected[0].chunk_id),
                            "quote": selected[0].content,
                        }
                    ],
                },
            ],
        },
        calls=calls,
        user_query="What are the AGM and filing duties?",
    )
    assert retrieval.retrieve.await_count == 2
    assert [call.kwargs["query"] for call in retrieval.retrieve.call_args_list] == [
        "annual return filing",
        "company duties overview",
    ]
    assert len(calls) == 2
    assert result.diagnostics["status"] == "recovered"
    assert {item.chunk_id for item in result.selected} == {selected[0].chunk_id, later.chunk_id}


async def test_delta_review_keeps_valid_facet_and_unresolved_obligation():
    known = chunk("Private companies must hold an annual general meeting.")
    later = chunk("An unreviewed annual filing rule.")
    coverage = {
        "complete": False,
        "missing": ["Annual return filing duty"],
        "checks": [
            {
                "requirement_id": "R1",
                "description": "Annual return filing duty",
                "supported": False,
                "evidence": [],
            },
            {
                "requirement_id": "R2",
                "description": "AGM duty",
                "supported": True,
                "evidence": [{"chunk_id": str(known.chunk_id), "quote": known.content}],
            },
        ],
        "partial_answer": {
            "scope": "AGM duty",
            "requirement_ids": ["R2"],
            "exclusions": ["Annual return filing duty"],
        },
    }
    calls = []
    result, retrieval, _ = await run_repair(
        [([known], {}), ([later], {})],
        queries=["company AGM"],
        requirements=[
            {"requirement_id": "R1", "description": "Annual return filing duty"},
            {"requirement_id": "R2", "description": "AGM duty"},
        ],
        coverage=coverage,
        followup_queries=["annual list summary"],
        final_coverage={
            "complete": False,
            "missing": ["Annual return filing duty", "penalty schedule for late filing"],
            "checks": [
                {
                    "requirement_id": "R1",
                    "description": "Renamed filing obligation",
                    "supported": False,
                    "evidence": [],
                }
            ],
            "partial_answer": coverage["partial_answer"],
        },
        calls=calls,
    )
    coverage_payloads = _coverage_review_payloads(calls)
    assert len(coverage_payloads) == 2
    second_review = coverage_payloads[1]
    assert second_review["review_mode"] == "changed_or_unresolved_facets"
    assert [item["requirement_id"] for item in second_review["requirements"]] == ["R1"]
    assert result.diagnostics["status"] == "partial_answer"
    assert "penalty schedule for late filing" in result.diagnostics["coverage"]["missing"]
    assert result.diagnostics["coverage"]["complete"] is False
    facets = {item["requirement_id"]: item for item in result.diagnostics["proof_map"]["facets"]}
    assert facets["R2"]["valid"] is True
    assert facets["R2"]["description"] == "AGM duty"
    assert facets["R1"]["valid"] is False
    assert facets["R1"]["description"] == "Annual return filing duty"
    assert [item.chunk_id for item in result.selected] == [known.chunk_id]
    assert retrieval.retrieve.await_count == 2


def test_unmatched_delta_missing_blocks_complete_verdict():
    from app.modules.conversations.services.evidence_coverage import CoverageDelta
    from app.modules.conversations.services.evidence_repair_service import (
        EvidenceRequirement,
        _TurnProofMap,
    )

    known = chunk("Private companies must hold an annual general meeting.")
    later = chunk("Private companies must file an annual return.")
    proof = _TurnProofMap(
        [
            EvidenceRequirement(requirement_id="R1", description="AGM duty"),
            EvidenceRequirement(requirement_id="R2", description="Filing duty"),
        ]
    )
    proof.accept_check(_supported_check("R1", known, "AGM duty"), [known, later], [])
    delta = CoverageDelta.model_validate(
        {
            "complete": True,
            "missing": ["penalty schedule for late filing"],
            "checks": [
                {
                    "requirement_id": "R2",
                    "description": "Filing duty",
                    "supported": True,
                    "evidence": [{"chunk_id": str(later.chunk_id), "quote": later.content}],
                }
            ],
        }
    )
    verdict = proof.merge_delta(delta, {"R2"}, [known, later], [])
    assert verdict.complete is False
    assert "penalty schedule for late filing" in verdict.missing
    assert proof.proven_ids() == {"R1", "R2"}
    assert verdict.validates([], [known, later], {"R1", "R2"}) is False


def _supported_check(requirement_id: str, source: ContextChunk, description: str = "") -> object:
    from app.modules.conversations.services.evidence_coverage import _Check

    return _Check.model_validate(
        {
            "requirement_id": requirement_id,
            "description": description or requirement_id,
            "supported": True,
            "evidence": [{"chunk_id": str(source.chunk_id), "quote": source.content}],
        }
    )


def test_shared_proof_dependency_invalidates_every_dependent_facet():
    from app.modules.conversations.services.evidence_repair_service import (
        EvidenceRequirement,
        _TurnProofMap,
    )

    shared = chunk(
        "Section 36: companies must hold an AGM and file an annual list.",
        source_revision_id="shared-revision",
    )
    proof = _TurnProofMap(
        [
            EvidenceRequirement(requirement_id="R1", description="AGM duty"),
            EvidenceRequirement(requirement_id="R2", description="Filing duty"),
            EvidenceRequirement(requirement_id="R3", description="Filing deadline"),
        ]
    )
    proof.accept_check(_supported_check("R1", shared, "AGM duty"), [shared], [])
    proof.accept_check(_supported_check("R2", shared, "Filing duty"), [shared], [])
    changed = replace(shared, content="Changed section 36 duties.")
    invalidated = proof.invalidate_changed([changed], [])
    assert invalidated == {"R1", "R2", "R3"}
    assert proof.proven_ids() == set()
    assert proof._facets["R1"].description == "AGM duty"
    assert proof._facets["R2"].description == "Filing duty"


def test_identical_authority_records_do_not_invalidate_validated_partial():
    from app.modules.conversations.services.evidence_repair_service import (
        EvidenceRequirement,
        _TurnProofMap,
    )

    known = chunk(
        "Private companies must hold an annual general meeting.",
        source_revision_id="agm-revision",
    )
    record = {
        "relationship_type": "related",
        "outcome": "expanded",
        "base_revision_id": "agm-revision",
        "source_revision_id": "agm-revision",
        "target_provisions": ["36"],
    }
    proof = _TurnProofMap(
        [
            EvidenceRequirement(requirement_id="R1", description="Annual return filing duty"),
            EvidenceRequirement(requirement_id="R2", description="AGM duty"),
        ]
    )
    proof.remember_records([record])
    proof.accept_check(_supported_check("R2", known, "AGM duty"), [known], [record])
    invalidated = proof.invalidate_changed([known], [record, dict(record)])
    assert "R2" not in invalidated or proof._facets["R2"].valid
    assert proof._facets["R2"].valid is True
    assert proof.proven_ids() == {"R2"}


def test_new_scoped_authority_reopens_only_affected_facet():
    from app.modules.conversations.services.evidence_repair_service import (
        EvidenceRequirement,
        _TurnProofMap,
    )

    first = chunk("AGM duty applies to private companies.", source_revision_id="agm-revision")
    second = chunk("Filing duty applies separately.", source_revision_id="filing-revision")
    proof = _TurnProofMap(
        [
            EvidenceRequirement(requirement_id="R1", description="AGM duty"),
            EvidenceRequirement(requirement_id="R2", description="Filing duty"),
            EvidenceRequirement(requirement_id="R3", description="Filing deadline"),
        ]
    )
    proof.accept_check(_supported_check("R1", first, "AGM duty"), [first, second], [])
    proof.accept_check(_supported_check("R2", second, "Filing duty"), [first, second], [])
    record = {
        "outcome": "expanded",
        "base_revision_id": "agm-revision",
        "target_provisions": ["Section 81 AGM"],
    }
    invalidated = proof.invalidate_changed([first, second], [record])
    assert "R1" in invalidated
    assert proof._facets["R1"].valid is False
    assert proof._facets["R2"].valid is True
    assert proof._facets["R2"].description == "Filing duty"
    assert "R3" in invalidated


async def test_identical_authority_records_skip_repeat_coverage_review():
    known = chunk(
        "Private companies must hold an annual general meeting.",
        source_revision_id="agm-revision",
    )
    coverage = {
        "complete": False,
        "missing": ["Annual return filing duty"],
        "checks": [
            {"requirement_id": "R1", "supported": False, "evidence": []},
            {
                "requirement_id": "R2",
                "description": "AGM duty",
                "supported": True,
                "evidence": [{"chunk_id": str(known.chunk_id), "quote": known.content}],
            },
        ],
        "partial_answer": {
            "scope": "AGM duty",
            "requirement_ids": ["R2"],
            "exclusions": ["Annual return filing duty"],
        },
    }
    record = {
        "relationship_type": "related",
        "outcome": "expanded",
        "base_revision_id": "agm-revision",
        "source_revision_id": "agm-revision",
        "target_provisions": ["36"],
    }
    calls = []
    result, retrieval, _ = await run_repair(
        [([known], {}), ([known], {"modifies_expansion_records": [record, record]})],
        queries=["company AGM"],
        requirements=[
            {"requirement_id": "R1", "description": "Annual return filing duty"},
            {"requirement_id": "R2", "description": "AGM duty"},
        ],
        coverage=coverage,
        followup_queries=["annual list summary"],
        initial_records=[record],
        final_verification_error=ProviderTimeoutError("Review timed out", provider_name="fake"),
        calls=calls,
    )
    assert result.diagnostics["status"] == "partial_answer"
    assert result.diagnostics.get("coverage_reviews_skipped") == 1
    assert len(_coverage_review_payloads(calls)) == 1
    assert retrieval.retrieve.await_count == 2
    assert [item.chunk_id for item in result.selected] == [known.chunk_id]


async def test_changed_shared_evidence_reviews_every_dependent_facet():
    shared = chunk(
        "Section 36: companies must hold an AGM and file an annual list.",
        source_revision_id="shared-revision",
    )
    coverage = {
        "complete": False,
        "missing": ["Filing deadline"],
        "checks": [
            {
                "requirement_id": "R1",
                "description": "AGM duty",
                "supported": True,
                "evidence": [{"chunk_id": str(shared.chunk_id), "quote": shared.content}],
            },
            {
                "requirement_id": "R2",
                "description": "Filing duty",
                "supported": True,
                "evidence": [{"chunk_id": str(shared.chunk_id), "quote": shared.content}],
            },
            {
                "requirement_id": "R3",
                "description": "Filing deadline",
                "supported": False,
                "evidence": [],
            },
        ],
        "partial_answer": {
            "scope": "AGM and filing duties",
            "requirement_ids": ["R1", "R2"],
            "exclusions": ["Filing deadline"],
        },
    }
    changed = replace(shared, content="Changed section 36 duties.")
    calls = []
    result, _, _ = await run_repair(
        [([shared], {}), ([changed], {})],
        queries=["section 36 duties"],
        requirements=[
            {"requirement_id": "R1", "description": "AGM duty"},
            {"requirement_id": "R2", "description": "Filing duty"},
            {"requirement_id": "R3", "description": "Filing deadline"},
        ],
        coverage=coverage,
        followup_queries=["section 36 deadline"],
        final_coverage={
            "complete": False,
            "missing": ["AGM duty", "Filing duty", "Filing deadline"],
            "checks": [
                {"requirement_id": "R1", "supported": False, "evidence": []},
                {"requirement_id": "R2", "supported": False, "evidence": []},
                {"requirement_id": "R3", "supported": False, "evidence": []},
            ],
        },
        calls=calls,
    )
    coverage_payloads = _coverage_review_payloads(calls)
    assert len(coverage_payloads) == 2
    assert {item["requirement_id"] for item in coverage_payloads[1]["requirements"]} == {
        "R1",
        "R2",
        "R3",
    }
    facets = {item["requirement_id"]: item for item in result.diagnostics["proof_map"]["facets"]}
    assert facets["R1"]["valid"] is False
    assert facets["R2"]["valid"] is False


async def test_new_scoped_authority_reviews_only_affected_requirements():
    first = chunk("AGM duty applies to private companies.", source_revision_id="agm-revision")
    second = chunk("Filing duty applies separately.", source_revision_id="filing-revision")
    coverage = {
        "complete": False,
        "missing": ["Filing deadline"],
        "checks": [
            {
                "requirement_id": "R1",
                "description": "AGM duty",
                "supported": True,
                "evidence": [{"chunk_id": str(first.chunk_id), "quote": first.content}],
            },
            {
                "requirement_id": "R2",
                "description": "Filing duty",
                "supported": True,
                "evidence": [{"chunk_id": str(second.chunk_id), "quote": second.content}],
            },
            {"requirement_id": "R3", "supported": False, "evidence": []},
        ],
        "partial_answer": {
            "scope": "Known duties",
            "requirement_ids": ["R1", "R2"],
            "exclusions": ["Filing deadline"],
        },
    }
    calls = []
    result, _, _ = await run_repair(
        [
            ([first, second], {}),
            (
                [first, second],
                {
                    "modifies_expansion_records": [
                        {
                            "relationship_type": "related",
                            "outcome": "expanded",
                            "base_revision_id": "agm-revision",
                            "target_provisions": ["Section 81 AGM"],
                        }
                    ]
                },
            ),
        ],
        queries=["company duties"],
        requirements=[
            {"requirement_id": "R1", "description": "AGM duty"},
            {"requirement_id": "R2", "description": "Filing duty"},
            {"requirement_id": "R3", "description": "Filing deadline"},
        ],
        coverage=coverage,
        followup_queries=["filing deadline"],
        final_coverage=coverage,
        calls=calls,
    )
    coverage_payloads = _coverage_review_payloads(calls)
    assert len(coverage_payloads) == 2
    reviewed = {item["requirement_id"] for item in coverage_payloads[1]["requirements"]}
    assert "R1" in reviewed
    assert "R3" in reviewed
    assert "R2" not in reviewed
    facets = {item["requirement_id"]: item for item in result.diagnostics["proof_map"]["facets"]}
    assert facets["R2"]["valid"] is True
    assert facets["R2"]["description"] == "Filing duty"


async def test_unknown_authority_scope_is_conservative():
    known = chunk(
        "Private companies must hold an annual general meeting.",
        source_revision_id="agm-revision",
    )
    coverage = {
        "complete": False,
        "missing": ["Annual return filing duty"],
        "checks": [
            {"requirement_id": "R1", "supported": False, "evidence": []},
            {
                "requirement_id": "R2",
                "description": "AGM duty",
                "supported": True,
                "evidence": [{"chunk_id": str(known.chunk_id), "quote": known.content}],
            },
        ],
        "partial_answer": {
            "scope": "AGM duty",
            "requirement_ids": ["R2"],
            "exclusions": ["Annual return filing duty"],
        },
    }
    result, _, _ = await run_repair(
        [
            ([known], {}),
            (
                [known],
                {
                    "modifies_expansion_records": [
                        {
                            "outcome": "ungoverned_or_incomplete_metadata",
                            "base_revision_id": "agm-revision",
                            "target_provisions": [],
                        }
                    ]
                },
            ),
        ],
        queries=["company AGM"],
        requirements=[
            {"requirement_id": "R1", "description": "Annual return filing duty"},
            {"requirement_id": "R2", "description": "AGM duty"},
        ],
        coverage=coverage,
        followup_queries=["annual list summary"],
        final_verification_error=ProviderTimeoutError("Review timed out", provider_name="fake"),
    )
    assert result.partial_answer is None
    assert result.decision is None


async def test_selector_only_retry_repairs_isolated_range_without_full_review():
    from app.modules.conversations.services.evidence_coverage import numbered_source_lines
    from app.platform.providers.contracts.llm import ChatMessage, ChatRole

    source = chunk("Catalogue\n\nThe premiere year is 1998.")
    first = ChatCompletionResult(
        content=json.dumps(
            {
                "complete": True,
                "missing": [],
                "checks": [
                    {
                        "requirement_id": "year",
                        "supported": True,
                        "evidence": [{"chunk_id": "E1", "start_line": 2, "end_line": 2}],
                    }
                ],
            }
        ),
        provider="fake",
        model="test",
        finish_reason="stop",
        usage=ChatUsage(1, 1),
        provider_version="1",
    )
    repair = replace(
        first,
        content=json.dumps(
            {
                "replacements": [
                    {
                        "requirement_id": "year",
                        "evidence_index": 0,
                        "chunk_id": "E1",
                        "start_line": 3,
                        "end_line": 3,
                    }
                ]
            }
        ),
    )
    llm = AsyncMock()
    llm.generate.side_effect = [first, repair]
    payload = {
        "original_question": "Select the evidence line",
        "context": [{"chunk_id": "E1", "content": numbered_source_lines(source.content)}],
    }
    result = await _validated_completion(
        llm,
        [ChatMessage(ChatRole.USER, json.dumps(payload))],
        schema=CoverageVerdict,
        max_tokens=1024,
        proof_context=[source],
        source_ids={"E1": str(source.chunk_id)},
    )
    assert llm.generate.await_count == 2
    assert "Failed selectors:" in llm.generate.call_args_list[1].args[0][-1].content
    parsed = json.loads(result.content)
    assert parsed["checks"][0]["evidence"][0]["start_line"] == 3
    assert parsed["complete"] is True


async def test_contradictory_verdict_keeps_bounded_full_retry():
    from app.platform.providers.contracts.llm import ChatMessage, ChatRole

    first = ChatCompletionResult(
        content=json.dumps({"complete": True, "missing": ["rule"], "checks": []}),
        provider="fake",
        model="test",
        finish_reason="stop",
        usage=ChatUsage(1, 1),
        provider_version="1",
    )
    retried = replace(
        first,
        content=json.dumps({"complete": False, "missing": ["rule"], "checks": []}),
    )
    llm = AsyncMock()
    llm.generate.side_effect = [first, retried]
    result = await _validated_completion(
        llm,
        [ChatMessage(ChatRole.USER, "Review the evidence")],
        schema=CoverageVerdict,
        max_tokens=1024,
    )
    assert llm.generate.await_count == 2
    assert "Validation issues:" in llm.generate.call_args_list[1].args[0][-1].content
    assert "Failed selectors:" not in llm.generate.call_args_list[1].args[0][-1].content
    assert json.loads(result.content)["complete"] is False


def test_snapshot_verdict_keeps_extra_missing_without_synthesizing_partial():
    from app.modules.conversations.services.evidence_repair_service import (
        EvidenceRequirement,
        _TurnProofMap,
    )

    known = chunk("Private companies must hold an annual general meeting.")
    proof = _TurnProofMap(
        [EvidenceRequirement(requirement_id="R1", description="AGM duty")]
    )
    proof.accept_check(_supported_check("R1", known, "AGM duty"), [known], [])
    proof.remember_gaps(["Required filing deadline still unknown"])
    verdict = proof.snapshot_verdict()
    assert verdict.complete is False
    assert "Required filing deadline still unknown" in verdict.missing
    assert verdict.partial_answer is None


def test_invalid_optional_planning_coverage_is_discarded():
    from app.modules.conversations.services.evidence_repair_service import _SearchPlan

    plan = _SearchPlan.model_validate(
        {
            "queries": [{"query": "annual return deadline", "requirement_ids": ["R1"]}],
            "requirements": [
                {
                    "requirement_id": "R1",
                    "description": "Annual return deadline",
                    "origin": "explicit_user_request",
                }
            ],
            "coverage": {
                "complete": True,
                "missing": ["Required filing deadline still unknown"],
                "checks": [],
            },
        }
    )
    assert [query.query for query in plan.queries] == ["annual return deadline"]
    assert plan.coverage is None


async def test_invalid_optional_plan_coverage_does_not_block_validated_completion():
    from app.modules.conversations.services.evidence_repair_service import (
        _SearchPlan,
        _validated_completion,
    )

    payload = {
        "queries": [{"query": "annual return deadline", "requirement_ids": ["R1"]}],
        "requirements": [
            {
                "requirement_id": "R1",
                "description": "Annual return deadline",
                "origin": "explicit_user_request",
            }
        ],
        "coverage": {
            "complete": True,
            "missing": ["Required filing deadline still unknown"],
            "checks": [],
        },
    }
    llm = AsyncMock()
    llm.generate.return_value = ChatCompletionResult(
        content=json.dumps(payload),
        provider="fake",
        model="test",
        finish_reason="stop",
        usage=ChatUsage(1, 1),
        provider_version="1",
    )
    response = await _validated_completion(llm, [], schema=_SearchPlan, max_tokens=1024)
    parsed = _SearchPlan.model_validate_json(response.content)
    assert llm.generate.await_count == 1
    assert parsed.coverage is None
    assert [query.query for query in parsed.queries] == ["annual return deadline"]


def test_selector_repair_matches_legacy_checks_by_query_index():
    from app.modules.conversations.services.evidence_repair_service import (
        _apply_selector_replacements,
        _SelectorRepairResponse,
    )

    parsed = CoverageVerdict.model_validate(
        {
            "complete": False,
            "missing": ["rule"],
            "checks": [
                {
                    "query_index": 0,
                    "supported": True,
                    "evidence": [
                        {
                            "chunk_id": "aaaa",
                            "start_line": 99,
                            "end_line": 99,
                        }
                    ],
                },
                {
                    "query_index": 1,
                    "supported": True,
                    "evidence": [
                        {
                            "chunk_id": "bbbb",
                            "start_line": 99,
                            "end_line": 99,
                        }
                    ],
                },
            ],
        }
    )
    original_first = parsed.checks[0].evidence[0].model_copy()
    repair = _SelectorRepairResponse.model_validate(
        {
            "replacements": [
                {
                    "query_index": 1,
                    "evidence_index": 0,
                    "chunk_id": "cccc",
                    "start_line": 1,
                    "end_line": 1,
                }
            ]
        }
    )
    failures = [
        {"requirement_id": None, "query_index": 1, "evidence_index": 0},
    ]
    assert _apply_selector_replacements(parsed, repair, failures) is True
    assert parsed.checks[0].evidence[0].chunk_id == original_first.chunk_id
    assert parsed.checks[0].evidence[0].start_line == original_first.start_line
    assert parsed.checks[1].evidence[0].chunk_id == "cccc"
    assert parsed.checks[1].evidence[0].start_line == 1
