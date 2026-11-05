"""Planner JSON-mode wire instructions agree with strict consumer validation."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.core.config import ChatConfig, RetrievalConfig
from app.modules.conversations.grounding_service import GroundingService
from app.modules.conversations.ports import ContextRetrievalResult
from app.modules.conversations.prompts.authoritative_compatibility import (
    AUTHORITATIVE_PLANNING_PROMPT,
)
from app.modules.conversations.prompts.evidence_repair import EVIDENCE_REPAIR_PROMPT
from app.modules.conversations.services.evidence_repair_service import (
    _SearchPlan,
    _validated_completion,
    repair_knowledge_evidence,
)
from app.modules.conversations.turn_resolution import EffectiveRetrievalInputs
from app.platform.providers.contracts.llm import ChatMessage, ChatRole
from app.platform.providers.request_work import RequestWork, current_request_purpose
from tests.unit.modules.conversations.test_phase2_completion import execute_contract
from tests.unit.modules.conversations.test_post_qa_contract import CapturedProvider, journey

pytestmark = pytest.mark.unit
PROMPTS = [AUTHORITATIVE_PLANNING_PROMPT, EVIDENCE_REPAIR_PROMPT]


def plan(mapping=None):
    return {
        "queries": [],
        "requirements": [
            {
                "requirement_id": "D",
                "description": "Defined governing category",
                "materiality": "governing_applicability",
                "depends_on": [],
                "dependency_purposes": {},
                "deliverable": False,
            },
            {
                "requirement_id": "R",
                "description": "Requested governing rule",
                "materiality": "central_rule",
                "depends_on": ["D"] if mapping else [],
                "dependency_purposes": {} if mapping is None else mapping,
            },
        ],
    }


def assert_wire_contract(body):
    assert body["response_format"] == {"type": "json_object"}
    assert "json_schema" not in body["response_format"]
    instructions = "\n".join(m["content"] for m in body["messages"] if m["role"] == "system")
    assert '{"D":"definition"}' in instructions
    assert '"dependency_purposes":{}' in instructions
    assert "prerequisite ID" in instructions
    assert "Only these root fields are allowed" in instructions
    for field in _SearchPlan.model_fields:
        assert field in instructions
    assert "coverage.partial_answer" in instructions
    assert "never a root field" in instructions


@pytest.mark.parametrize("prompt", PROMPTS, ids=["authoritative", "general"])
@pytest.mark.parametrize("mapping", [{}, {"D": "definition"}], ids=["empty", "populated"])
async def test_first_planner_call_transmits_contract_through_real_json_mode_builder(
    prompt, mapping
):
    class Planner(CapturedProvider):
        async def generate(self, messages, **kwargs):
            result = await super().generate(messages, **kwargs)
            assert_wire_contract(self.bodies[-1])
            assert self.bodies[-1]["messages"][0]["content"] == prompt
            return replace(result, content=json.dumps(plan(mapping)))

    provider = Planner(journey()[1].source)
    work = RequestWork(uuid.uuid4())
    with work.attached():
        result = await _validated_completion(
            provider,
            [ChatMessage(role=ChatRole.SYSTEM, content=prompt)],
            schema=_SearchPlan,
            max_tokens=1024,
            call_purpose="recovery_planning",
        )
    assert (
        _SearchPlan.model_validate_json(result.content).requirements[1].dependency_purposes
        == mapping
    )
    assert len(provider.bodies) == 1
    assert work.counts["structured_response_retries"] == 0


@pytest.mark.parametrize("invalid", [["definition"], [{"D": "definition"}], None, "definition"])
def test_dependency_purpose_invalid_shapes_remain_strict(invalid):
    payload = plan({"D": "definition"})
    payload["requirements"][1]["dependency_purposes"] = invalid
    with pytest.raises(ValidationError) as raised:
        _SearchPlan.model_validate(payload)
    assert any(
        e["loc"] == ("requirements", 1, "dependency_purposes") and e["type"] == "dict_type"
        for e in raised.value.errors()
    )


@pytest.mark.parametrize("fault", ["undeclared", "foreign", "purpose", "root_partial"])
def test_dependency_and_root_guards_remain_strict(fault):
    payload = plan({"D": "definition"})
    if fault == "undeclared":
        payload["requirements"][1]["dependency_purposes"] = {"unknown": "definition"}
    elif fault == "foreign":
        payload["requirements"][1].update(
            depends_on=["unknown"], dependency_purposes={"unknown": "definition"}
        )
    elif fault == "purpose":
        payload["requirements"][1]["dependency_purposes"] = {"D": "guessed"}
    else:
        payload["partial_answer"] = {"scope": "Wrong root"}
    with pytest.raises(ValidationError):
        _SearchPlan.model_validate(payload)


@pytest.mark.parametrize("termination", ["invalid_retry", "cancelled_retry", "deadline"])
async def test_invalid_mapping_has_only_one_retry_and_propagates_cancellation(termination):
    class MalformedPlanner(CapturedProvider):
        async def generate(self, messages, **kwargs):
            result = await super().generate(messages, **kwargs)
            if len(self.bodies) == 2:
                assert "Schema:" in messages[-1].content
                if termination == "cancelled_retry":
                    raise asyncio.CancelledError("controlled recovery cutoff")
                if termination == "deadline":
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        self.retry_cancelled = True
                        raise
            payload = plan({"D": "definition"})
            payload["requirements"][1]["dependency_purposes"] = ["definition"]
            return replace(result, content=json.dumps(payload))

    provider = MalformedPlanner(journey()[1].source)
    work = RequestWork(uuid.uuid4())

    async def validate():
        return await _validated_completion(
            provider,
            [ChatMessage(role=ChatRole.SYSTEM, content=AUTHORITATIVE_PLANNING_PROMPT)],
            schema=_SearchPlan,
            max_tokens=1024,
            call_purpose="recovery_planning",
        )

    expected_error = {
        "invalid_retry": ValidationError,
        "cancelled_retry": asyncio.CancelledError,
        "deadline": TimeoutError,
    }[termination]
    with work.attached(), pytest.raises(expected_error):
        if termination == "deadline":
            await asyncio.wait_for(validate(), timeout=0.05)
        else:
            await validate()
    assert len(provider.bodies) == 2
    assert work.counts["structured_response_retries"] == 1
    assert work.validation_retries[0]["issues"] == [
        {"type": "dict_type", "path": "requirements.1.dependency_purposes"}
    ]
    if termination == "deadline":
        assert provider.retry_cancelled is True


@pytest.mark.parametrize("transport", ["regular", "sse", "get"])
async def test_first_call_initial_assessment_retains_independent_rules_and_input(transport):
    service, provider, conversation, repository, _ = journey()
    question = (
        "Under Companies Act 1994, explain the first AGM deadline and maximum interval "
        "between subsequent AGMs, and decide if my company's first AGM is late. "
        "I have not supplied incorporation date."
    )
    asked = "What is your company's incorporation date?"
    generate_original = provider.generate
    planner_payloads = []

    async def generate(messages, **kwargs):
        result = await generate_original(messages, **kwargs)
        if current_request_purpose() == "recovery_planning":
            assert AUTHORITATIVE_PLANNING_PROMPT in messages[0].content
            assert_wire_contract(provider.bodies[-1])
            payload = json.loads(result.content)
            for row in payload["requirements"]:
                row.update(depends_on=[], dependency_purposes={})
            payload["requirements"].append(
                {
                    "requirement_id": "R3",
                    "description": "Determine personal first AGM lateness",
                    "task_kind": "personal_eligibility",
                    "origin": "explicit_user_request",
                    "materiality": "adjacent_rule",
                    "depends_on": ["R1"],
                    "dependency_purposes": {"R1": "applicability"},
                }
            )
            payload["coverage"].update(
                complete=False,
                missing=["Personal lateness conclusion"],
                partial_answer={
                    "scope": "Independently established AGM deadlines",
                    "requirement_ids": ["R1", "R2"],
                    "exclusions": ["Personal lateness conclusion"],
                },
            )
            payload["coverage"]["checks"].append(
                {"requirement_id": "R3", "supported": False, "fulfillment": "none", "evidence": []}
            )
            payload["missing_user_inputs"] = [
                {
                    "kind": "event_date",
                    "question": asked,
                    "requirement_ids": ["R3"],
                    "request_quote": question,
                }
            ]
            planner_payloads.append(_SearchPlan.model_validate(payload))
            result = replace(result, content=json.dumps(payload))
        return result

    provider.generate = generate
    answer = await execute_contract(
        service, provider, conversation, repository, question, transport
    )
    assert len(planner_payloads) == 1
    assert "structured_response_retry" not in provider.purposes
    assert planner_payloads[0].requirements[-1].dependency_purposes == {"R1": "applicability"}
    assert answer["grounded"] is True and len(answer["claims"]) == 2
    assert "18 months" in answer["content"] and "15 months" in answer["content"]
    assert answer["content"].count(asked) == 1
    assert all(asked not in claim["text"] for claim in answer["claims"])
    assert answer["terminal_outcome"]["outcome"] == "needs_input"
    assert any(
        notice["kind"] == "missing_scenario_input" and notice["source"]["missing_inputs"] == [asked]
        for notice in answer["notices"]
    )


async def test_general_planner_initial_assessment_retains_requirements_and_input_obligation():
    question = (
        "Explain the general rule and decide my eligibility. I have not supplied my category."
    )
    asked = "Which personal category applies?"
    payload = plan({"D": "definition"})
    payload["requirements"].append(
        {
            "requirement_id": "P",
            "description": "Decide personal eligibility",
            "materiality": "central_rule",
            "task_kind": "personal_eligibility",
            "depends_on": ["R"],
            "dependency_purposes": {"R": "applicability"},
        }
    )
    payload["missing_user_inputs"] = [
        {
            "kind": "category",
            "question": asked,
            "requirement_ids": ["P"],
            "request_quote": question,
        }
    ]

    class Planner(CapturedProvider):
        async def generate(self, messages, **kwargs):
            result = await super().generate(messages, **kwargs)
            assert EVIDENCE_REPAIR_PROMPT in messages[0].content
            assert_wire_contract(self.bodies[-1])
            return replace(result, content=json.dumps(payload))

    provider = Planner(journey()[1].source)
    retrieval = AsyncMock()
    initial = ContextRetrievalResult(
        [], {"index_build_id": "controlled", "source_metadata_generation": 1}
    )
    with RequestWork(uuid.uuid4()).attached():
        result = await repair_knowledge_evidence(
            inputs=EffectiveRetrievalInputs(query=question),
            initial=initial,
            selected=[],
            retrieval=retrieval,
            llm=provider,
            grounding=GroundingService(ChatConfig()),
            chat_config=ChatConfig(),
            retrieval_config=RetrievalConfig(),
            evidence_approach="factual",
            max_output_tokens=4096,
            max_followup_queries=0,
            max_followup_rounds=0,
        )
    assessment = result.diagnostics["initial_assessment"]
    assert [row["requirement_id"] for row in assessment["requirements"]] == ["D", "R", "P"]
    assert assessment["requirements"][1]["dependency_purposes"] == {"D": "definition"}
    assert assessment["input_obligations"] == payload["missing_user_inputs"]
    assert result.missing_inputs == (asked,)
    assert assessment["coverage_validated"] is False and assessment["evidence_bindings"] == []
    assert len(provider.bodies) == 1
    retrieval.retrieve.assert_not_awaited()
