"""Evidence approach compatibility, diversity, and stable requirement proofs."""

import uuid
from dataclasses import replace

import pytest

from app.core.config import ChatConfig, Settings
from app.modules.conversations.context_builder import (
    ContextBuilder,
    historical_scope_requested,
    reviewed_work_count,
)
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.prompts.registry import require_prompt_template
from app.modules.conversations.services.evidence_coverage import CoverageVerdict
from app.platform.config.project_ai import (
    ConfigRevisionRecord,
    resolve_project_ai_config,
    stable_hash,
)
from app.platform.providers.contracts.llm import ChatMessage, ChatRole
from app.platform.providers.prompt_budget import prompt_budget


def chunk(work: str, text: str = "An account of the event.") -> ContextChunk:
    return ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=text,
        score=0.9,
        filename=work,
        chunk_hash=str(uuid.uuid4()),
        metadata={"source_work_key": work},
    )


@pytest.mark.parametrize("approach", ["factual", "authoritative", "multi_perspective"])
def test_configuration_round_trip_and_legacy_default(approach):
    settings = Settings()
    old = resolve_project_ai_config(settings, None).configuration
    assert old.evidence_approach == "authoritative"
    payload = {"behavior": {"evidence_approach": approach}, "execution": {}}
    revision = ConfigRevisionRecord(
        id=uuid.uuid4(),
        revision_number=1,
        schema_version=2,
        configuration=payload,
        configuration_hash=stable_hash(payload),
    )
    resolution = resolve_project_ai_config(settings, revision)
    assert resolution.configuration.evidence_approach == approach
    assert resolution.configuration.retrieval == old.retrieval
    assert resolution.structured_origins["evidence_approach"].owner == "project"
    template = require_prompt_template("legacy", evidence_approach=approach)
    if approach == "authoritative":
        assert "gross" in template.template and "floors" in template.template
    else:
        assert "independent works" in template.template


def test_diversity_preserves_minority_and_requested_work_without_vote_inflation():
    repeated = [chunk("Account A", f"Passage {i}") for i in range(8)]
    minority = chunk("Account B")
    selected = ContextBuilder(
        ChatConfig(max_context_chunks=2),
        evidence_approach="multi_perspective",
        question="Compare Account B and Account A",
    ).select([*repeated, minority])
    assert {c.filename for c in selected} == {"Account A", "Account B"}
    translated = replace(repeated[0], chunk_id=uuid.uuid4(), filename="Translation")
    assert reviewed_work_count([repeated[0], translated]) == 1
    copy = replace(
        minority, metadata={"source_work_key": "other", "source_content_hash": "same-file"}
    )
    original = replace(
        repeated[0], metadata={"source_work_key": "original", "source_content_hash": "same-file"}
    )
    assert reviewed_work_count([original, copy]) == 1


def test_disagreement_proof_counts_requirements_not_search_routes():
    a, b = chunk("A", "The event happened in spring."), chunk("B", "The event happened in autumn.")
    verdict = CoverageVerdict.model_validate(
        {
            "complete": True,
            "missing": [],
            "checks": [
                {
                    "requirement_id": "positions",
                    "description": "Compare both accounts",
                    "supported": True,
                    "evidence": [
                        {"chunk_id": str(c.chunk_id), "start_line": 1, "end_line": 1}
                        for c in [a, b]
                    ],
                }
            ],
        }
    )
    assert verdict.resolve_source_ranges([a, b])
    assert verdict.validates([[], [], [], []], [a, b], {"positions"})
    assert not verdict.validates([], [a], {"positions"})
    assert not verdict.validates([], [a, b], {"positions", "missing_dependency"})


def test_unknown_tokenizer_uses_multilingual_byte_bound_and_reserves_output():
    messages = [ChatMessage(ChatRole.USER, "বাংলা শব্দ")]
    budget = prompt_budget(messages, model="unregistered-model", capacity=150, reserved_output=30)
    assert budget["count_method"] == "utf8_byte_upper_bound"
    assert budget["text_tokens"] == len("বাংলা শব্দ".encode())
    assert not budget["within_budget"]


def test_copy_identity_unions_translations_transitively_in_any_order():
    a = chunk("A")
    b = replace(chunk("B"), metadata={"source_work_key": "B", "source_content_hash": "copy"})
    translated = replace(a, metadata={"source_work_key": "A", "source_content_hash": "copy"})
    assert reviewed_work_count([a, b, translated]) == 1
    assert reviewed_work_count([translated, b, a]) == 1


def test_historical_cues_do_not_classify_publication_years_as_cutoffs():
    assert historical_scope_requested(
        "What rules applied in historical AY 2024-25?", "authoritative"
    )
    assert historical_scope_requested("As of 2025-06-30, what rules applied?", "factual")
    assert not historical_scope_requested("Who directed the film released in 1998?", "factual")
    assert not historical_scope_requested(
        "Compare the 1998 and 2001 accounts.", "multi_perspective"
    )
    assert not historical_scope_requested("Calculate tax for AY 2026-27.", "authoritative")
