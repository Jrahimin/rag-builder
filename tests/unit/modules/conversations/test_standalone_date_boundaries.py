"""Current-message date scope survives unrelated history without adopting it."""

import uuid
from datetime import UTC, datetime

import pytest

from app.core.calendar import ProjectCalendarPolicy
from app.modules.conversations.turn_resolution import (
    RequestFilters,
    TurnOutcome,
    TurnResolutionInput,
    normalize_request_scope,
)
from app.modules.conversations.turn_resolver import TurnResolver
from tests.unit.modules.conversations.test_turn_resolver import JsonLLM

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("history", [False, True])
@pytest.mark.parametrize(
    ("message", "kind", "expected", "basis"),
    [
        ("What rate applied as of 2025-07-01?", "exact_date", "2025-07-01", "applicable_rule"),
        (
            "What rate applied on the day before 2025-07-01?",
            "day_before_identified_date",
            "2025-06-30",
            "applicable_rule",
        ),
        ("What rate was known as of 2025-07-01?", "exact_date", "2025-07-01", "known_at"),
        (
            "\u09e8\u09e6\u09e8\u09eb-\u09e6\u09ed-\u09e6\u09e7 তারিখে হার কত ছিল?",
            "exact_date",
            "2025-07-01",
            "applicable_rule",
        ),
        (
            "\u09e8\u09e6\u09e8\u09eb-\u09e6\u09ed-\u09e6\u09e7 তারিখের আগের দিন হার কত ছিল?",
            "day_before_identified_date",
            "2025-06-30",
            "applicable_rule",
        ),
        (
            "What rate was known as of \u09e8\u09e6\u09e8\u09eb-\u09e6\u09ed-\u09e6\u09e7?",
            "exact_date",
            "2025-07-01",
            "known_at",
        ),
    ],
)
async def test_current_date_survives_history(message, kind, expected, basis, history):
    current_id = uuid.uuid4()
    payload = TurnResolutionInput.model_validate(
        {
            "current_message_id": current_id,
            "current_message": message,
            "history": [
                {"id": uuid.uuid4(), "role": "user", "content": "Describe the registration steps."}
            ]
            if history
            else [],
            "reference_time": datetime(2026, 8, 1, tzinfo=UTC),
        }
    )
    result = await TurnResolver(
        JsonLLM(
            {
                "outcome": "standalone",
                "relation": "standalone",
                "effective_question": message,
                "active_bindings": [
                    {
                        "kind": "period_date",
                        "active_value": "2025-07-01",
                        "origin": "user_literal",
                        "references": [
                            {
                                "message_id": str(current_id),
                                "role": "user",
                                "field": "content",
                                "excerpt": message,
                            }
                        ],
                    }
                ],
                "temporal_intent": {
                    "kind": kind,
                    "anchor_date": "2025-07-01",
                    "requires_snapshot": True,
                },
            }
        ),
        timeout_seconds=2,
        max_output_tokens=512,
    ).resolve(payload)
    assert result.resolution.outcome is TurnOutcome.STANDALONE
    assert result.retrieval.as_of == datetime.fromisoformat(expected).replace(tzinfo=UTC)
    assert len(result.resolution.active_bindings) == 1
    # The final runner canonicalizes using effective retrieval filters and calendar.
    scope = normalize_request_scope(
        result.retrieval.query,
        reference_time=payload.reference_time,
        request_filters=RequestFilters(as_of=result.retrieval.as_of),
        calendar_policy=ProjectCalendarPolicy(
            timezone="Asia/Dhaka", period_kind="fiscal", boundary_month=7, boundary_day=1
        ),
    )
    assert scope.exact_as_of == result.retrieval.as_of
    assert scope.temporal_basis == basis
    assert scope.requested_periods == ()


@pytest.mark.parametrize("origin", ["user_literal", "assistant_reference"])
async def test_standalone_does_not_adopt_history_date(origin):
    prior_id = uuid.uuid4()
    role = "user" if origin == "user_literal" else "assistant"
    payload = TurnResolutionInput.model_validate(
        {
            "current_message_id": uuid.uuid4(),
            "current_message": "What rate applies?",
            "history": [
                {"id": prior_id, "role": role, "content": "The earlier date was 2025-07-01."}
            ],
            "reference_time": datetime(2026, 8, 1, tzinfo=UTC),
        }
    )
    result = await TurnResolver(
        JsonLLM(
            {
                "outcome": "standalone",
                "relation": "standalone",
                "effective_question": "What rate applied as of 2025-07-01?",
                "active_bindings": [
                    {
                        "kind": "period_date",
                        "active_value": "2025-07-01",
                        "origin": origin,
                        "references": [
                            {"message_id": str(prior_id), "role": role, "field": "content"}
                        ],
                    }
                ],
                "temporal_intent": {
                    "kind": "exact_date",
                    "anchor_date": "2025-07-01",
                    "requires_snapshot": True,
                },
            }
        ),
        timeout_seconds=2,
        max_output_tokens=512,
    ).resolve(payload)
    assert result.retrieval.query == payload.current_message
    assert result.retrieval.as_of is None
    assert not result.resolution.active_bindings


@pytest.mark.parametrize(
    ("suffix", "kind", "expected", "basis"),
    [
        ("as of 2025-07-01", "exact_date", "2025-07-01", "applicable_rule"),
        (
            "as of the day before 2025-07-01",
            "day_before_identified_date",
            "2025-06-30",
            "applicable_rule",
        ),
        ("known as of 2025-07-01", "exact_date", "2025-07-01", "known_at"),
        (
            "as of \u09e8\u09e6\u09e8\u09eb-\u09e6\u09ed-\u09e6\u09e7",
            "exact_date",
            "2025-07-01",
            "applicable_rule",
        ),
        (
            "known as of \u09e8\u09e6\u09e8\u09eb-\u09e6\u09ed-\u09e6\u09e7",
            "exact_date",
            "2025-07-01",
            "known_at",
        ),
    ],
)
@pytest.mark.parametrize("history", [False, True])
async def test_production_runner_forwards_final_date_and_canonical_scope(
    suffix, kind, expected, basis, history
):
    import json
    from unittest.mock import MagicMock

    from app.models.message import Message, MessageRole
    from app.modules.conversations.schemas.message import MessageSendRequest
    from app.platform.providers.request_work import current_request_purpose
    from tests.unit.modules.conversations.test_post_qa_contract import journey

    service, provider, conversation, repository, question = journey()
    service._chat_config.calendar_policy = ProjectCalendarPolicy(
        timezone="Asia/Dhaka", period_kind="fiscal", boundary_month=7, boundary_day=1
    )
    service._retrieval.set_request_scope = MagicMock()
    repository.list_recent_for_conversation.return_value = [
        Message(
            id=uuid.uuid4(),
            project_id=conversation.project_id,
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content="Describe the registration steps.",
            citations=[],
            message_metadata={},
            created_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
    ]
    if not history:
        repository.list_recent_for_conversation.return_value = []
    original = provider.generate

    async def generate(messages, **kwargs):
        if current_request_purpose() == "turn_resolution":
            body = json.loads(messages[1].content)
            message = body["current_message"]
            response = JsonLLM(
                {
                    "outcome": "standalone",
                    "relation": "standalone",
                    "effective_question": message,
                    "active_bindings": [
                        {
                            "kind": "period_date",
                            "active_value": "2025-07-01",
                            "origin": "user_literal",
                            "references": [
                                {
                                    "message_id": body["current_message_id"],
                                    "role": "user",
                                    "field": "content",
                                    "excerpt": message,
                                }
                            ],
                        }
                    ],
                    "temporal_intent": {
                        "kind": kind,
                        "anchor_date": "2025-07-01",
                        "requires_snapshot": True,
                    },
                }
            )
            return await response.generate(
                messages, temperature=None, max_tokens=kwargs["max_tokens"]
            )
        return await original(messages, **kwargs)

    provider.generate = generate
    await service.send_message(
        conversation.id, MessageSendRequest(content=question + " " + suffix + "?")
    )
    cutoff = datetime.fromisoformat(expected).replace(tzinfo=UTC)
    assert service._retrieval.retrieve.call_args.kwargs["as_of"] == cutoff
    scope = service._retrieval.set_request_scope.call_args.args[0]
    assert scope["exact_as_of"] == cutoff.isoformat().replace("+00:00", "Z")
    assert scope["temporal_basis"] == basis
    assert scope["requested_periods"] == []
    assert (
        service._work.evidence_snapshot["normalized_scope"]["exact_as_of"] == scope["exact_as_of"]
    )


@pytest.mark.parametrize("current_has_date", [False, True])
async def test_current_reference_must_attest_active_date_itself(current_has_date):
    prior_id = uuid.uuid4()
    current_id = uuid.uuid4()
    message = "Check 2025-07-01." if current_has_date else "What rate applies?"
    payload = TurnResolutionInput.model_validate(
        {
            "current_message_id": current_id,
            "current_message": message,
            "history": [
                {"id": prior_id, "role": "user", "content": "The earlier date was 2025-07-01."}
            ],
            "reference_time": datetime(2026, 8, 1, tzinfo=UTC),
        }
    )
    result = await TurnResolver(
        JsonLLM(
            {
                "outcome": "standalone",
                "relation": "standalone",
                "effective_question": message,
                "active_bindings": [
                    {
                        "kind": "period_date",
                        "active_value": "2025-07-01",
                        "origin": "user_literal",
                        "references": [
                            {"message_id": str(prior_id), "role": "user", "field": "content"},
                            {
                                "message_id": str(current_id),
                                "role": "user",
                                "field": "content",
                                "excerpt": message,
                            },
                        ],
                    }
                ],
                "temporal_intent": {
                    "kind": "exact_date",
                    "anchor_date": "2025-07-01",
                    "requires_snapshot": True,
                },
            }
        ),
        timeout_seconds=2,
        max_output_tokens=512,
    ).resolve(payload)
    assert bool(result.retrieval.as_of) is current_has_date
    assert bool(result.resolution.active_bindings) is current_has_date
