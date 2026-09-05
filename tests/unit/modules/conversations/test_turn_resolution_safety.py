"""Adversarial provenance and scope checks for bounded turn resolution."""

import uuid
from datetime import UTC, date, datetime

import pytest

from app.modules.conversations.turn_resolution import (
    BindingReference,
    CitationIdentity,
    EffectiveSnapshot,
    HistoryMessage,
    ReferenceBinding,
    RequestFilters,
    TemporalIntent,
    TurnResolution,
    TurnResolutionError,
    TurnResolutionInput,
    effective_retrieval_inputs,
    parameter_values_match,
    resolve_effective_as_of,
    validate_turn_resolution,
)

pytestmark = pytest.mark.unit
REFERENCE = datetime(2026, 8, 1, tzinfo=UTC)


def _payload(content="Use that amount.", history=(), citations=()):
    return TurnResolutionInput(
        current_message_id=uuid.uuid4(),
        current_message=content,
        history=list(history),
        citation_metadata=list(citations),
        reference_time=REFERENCE,
    )


def _resolution(bindings=(), **kwargs):
    return TurnResolution(
        outcome="resolved",
        relation="follow_up",
        effective_question="Calculate the fee.",
        active_bindings=list(bindings),
        **kwargs,
    )


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("-7500", "7500"),
        ("75.00", "7500"),
        ("10%", "10"),
        ("BDT 7500", "USD 7500"),
        ("10 hours", "10 days"),
        ("2025-01-01", "2025-10-1"),
    ],
)
def test_parameter_matching_preserves_meaning(left, right):
    assert not parameter_values_match(left, right)


@pytest.mark.parametrize(
    ("literal", "invented"),
    [
        ("-7,500", "7500"),
        ("\u22127,500", "7500"),
        ("10\u066b5", "10"),
        ("75.00", "7500"),
        ("75000", "7500"),
        ("10%", "10"),
        ("BDT 7500", "USD 7500"),
        ("10 hours", "10 days"),
    ],
)
def test_existing_reference_does_not_authorize_changed_value(literal, invented):
    payload = _payload(f"Use {literal}.")
    with pytest.raises(TurnResolutionError, match="Active value"):
        validate_turn_resolution(
            _resolution(
                [
                    ReferenceBinding(
                        kind="scenario_parameter",
                        active_value=invented,
                        origin="user_literal",
                        references=[
                            BindingReference(
                                message_id=payload.current_message_id,
                                role="user",
                                excerpt=literal,
                            )
                        ],
                    )
                ]
            ),
            payload,
        )


def test_unicode_correction_accepts_new_operand_without_old_operand():
    correction = "\u09ef\u09e6,\u09e6\u09e6\u09e6, not \u09ed\u09eb,\u09e6\u09e6\u09e6"
    payload = _payload(f"Use {correction}.")
    validated = validate_turn_resolution(
        _resolution(
            [
                ReferenceBinding(
                    kind="scenario_parameter",
                    active_value="90,000",
                    origin="user_literal",
                    references=[
                        BindingReference(
                            message_id=payload.current_message_id,
                            role="user",
                            excerpt=correction,
                        )
                    ],
                )
            ]
        ),
        payload,
    )
    assert [item.active_value for item in validated.active_bindings] == ["90,000"]


@pytest.mark.parametrize("kind", ["exact_date", "day_before_identified_date"])
@pytest.mark.parametrize("origin", ["user_literal", "user_adopted", "request_as_of"])
def test_claimed_origin_cannot_authorize_unreferenced_date(kind, origin):
    snapshot = resolve_effective_as_of(
        request_as_of=None,
        temporal_intent=TemporalIntent(
            kind=kind,
            anchor_date=date(2025, 6, 1),
            requires_snapshot=True,
            snapshot_origin=origin,
        ),
        bindings=[
            ReferenceBinding(
                kind="period_date",
                active_value="2026-07-01",
                origin="user_literal",
                references=[BindingReference(message_id=uuid.uuid4(), role="user")],
            )
        ],
        reference_time=REFERENCE,
    )
    assert snapshot.clarify and snapshot.as_of is None


@pytest.mark.parametrize("user_position", ["missing", "before"])
def test_adoption_requires_a_later_user_instruction(user_position):
    earlier_user, assistant = uuid.uuid4(), uuid.uuid4()
    payload = _payload(
        "Was that correct?",
        history=[
            HistoryMessage(id=earlier_user, role="user", content="Use that amount."),
            HistoryMessage(id=assistant, role="assistant", content="7500 or 9000"),
        ],
    )
    refs = [BindingReference(message_id=assistant, role="assistant", excerpt="7500")]
    if user_position == "before":
        refs.append(BindingReference(message_id=earlier_user, role="user", excerpt="Use that"))
    with pytest.raises(TurnResolutionError, match=r"user.*instruction"):
        validate_turn_resolution(
            _resolution(
                [
                    ReferenceBinding(
                        kind="scenario_parameter",
                        active_value="7500",
                        origin="user_adopted_assistant",
                        references=refs,
                    )
                ]
            ),
            payload,
        )


@pytest.mark.parametrize("field", ["content", "source_effective_from"])
def test_citation_references_validate_the_actual_field(field):
    assistant = uuid.uuid4()
    payload = _payload(
        "Compare those sources.",
        history=[
            HistoryMessage(id=assistant, role="assistant", content="The policy applies."),
        ],
        citations=[CitationIdentity(message_id=assistant, source_published_date=date(2025, 6, 1))],
    )
    with pytest.raises(TurnResolutionError, match=r"[Cc]itation"):
        validate_turn_resolution(
            _resolution(
                [
                    ReferenceBinding(
                        kind="period_date",
                        active_value="2025-06-01",
                        origin="citation_reference",
                        references=[
                            BindingReference(
                                message_id=assistant,
                                role="assistant",
                                field="citation",
                                citation_field=field,
                            )
                        ],
                    )
                ]
            ),
            payload,
        )


@pytest.mark.parametrize("adopted", [True, False])
def test_citation_date_needs_explicit_adoption_to_authorize_snapshot(adopted):
    assistant = uuid.uuid4()
    payload = _payload(
        "Use that publication date.",
        history=[
            HistoryMessage(id=assistant, role="assistant", content="The policy applies."),
        ],
        citations=[CitationIdentity(message_id=assistant, source_published_date=date(2025, 6, 1))],
    )
    refs = [
        BindingReference(
            message_id=assistant,
            role="assistant",
            field="citation",
            citation_field="source_published_date",
            excerpt="2025-06-01",
        )
    ]
    if adopted:
        refs.append(
            BindingReference(
                message_id=payload.current_message_id,
                role="user",
                excerpt="Use that publication date",
            )
        )
    resolution = validate_turn_resolution(
        _resolution(
            [
                ReferenceBinding(
                    kind="period_date",
                    active_value="2025-06-01",
                    origin="user_adopted_assistant" if adopted else "citation_reference",
                    references=refs,
                )
            ],
            temporal_intent=TemporalIntent(
                kind="exact_date",
                anchor_date=date(2025, 6, 1),
                requires_snapshot=True,
                snapshot_origin="user_literal",  # An asserted origin never grants authority.
            ),
        ),
        payload,
    )
    snapshot = resolve_effective_as_of(
        request_as_of=None,
        temporal_intent=resolution.temporal_intent,
        bindings=resolution.active_bindings,
        reference_time=REFERENCE,
    )
    assert snapshot.as_of == (datetime(2025, 6, 1, tzinfo=UTC) if adopted else None)
    assert snapshot.clarify is not adopted


def test_request_filters_are_authoritative_and_not_sticky():
    filters = RequestFilters(
        document_id=uuid.uuid4(),
        metadata_filter={"source": "expense_policy_2025"},
        as_of=datetime(2025, 6, 1, tzinfo=UTC),
    )
    for current in (filters, RequestFilters()):
        inputs = effective_retrieval_inputs(
            original_message="Compare the shares.",
            resolution=_resolution(),
            request_filters=current,
            snapshot=EffectiveSnapshot(),
        )
        assert inputs.document_id == current.document_id
        assert inputs.metadata_filter == current.metadata_filter
        assert inputs.as_of == current.as_of
