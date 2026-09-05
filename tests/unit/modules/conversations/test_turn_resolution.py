"""Pure tests for turn-resolution contracts and deterministic date/binding helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.modules.conversations.turn_resolution import (
    BindingKind,
    BindingOrigin,
    BindingReference,
    EffectiveSnapshot,
    HistoryMessage,
    ReferenceBinding,
    RequestFilters,
    TemporalIntent,
    TemporalIntentKind,
    TurnOutcome,
    TurnRelation,
    TurnResolution,
    TurnResolutionError,
    TurnResolutionInput,
    bindings_after_topic_change,
    bound_resolution_history,
    calendar_day_before,
    effective_retrieval_inputs,
    normalize_parameter_value,
    parameter_values_match,
    parse_iso_calendar_date,
    parse_resolver_json,
    referenced_message_ids,
    replace_active_parameter,
    resolve_effective_as_of,
    utc_midnight,
    utc_reference_datetime,
    validate_turn_resolution,
)

pytestmark = pytest.mark.unit

_REFERENCE = datetime(2026, 8, 1, tzinfo=UTC)


def _user_ref(message_id: uuid.UUID, excerpt: str | None = None) -> BindingReference:
    return BindingReference(message_id=message_id, role="user", excerpt=excerpt)


def _assistant_ref(message_id: uuid.UUID, excerpt: str | None = None) -> BindingReference:
    return BindingReference(message_id=message_id, role="assistant", excerpt=excerpt)


def _payload(
    *,
    current_id: uuid.UUID,
    current_message: str = "Use that amount as my next eligible investment.",
    history: list[HistoryMessage] | None = None,
    filters: RequestFilters | None = None,
    reference_time: datetime = _REFERENCE,
) -> TurnResolutionInput:
    return TurnResolutionInput(
        current_message_id=current_id,
        current_message=current_message,
        history=history or [],
        request_filters=filters or RequestFilters(),
        reference_time=reference_time,
    )


def test_adoption_requires_assistant_value_and_user_instruction() -> None:
    assistant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    history = [
        HistoryMessage(
            id=assistant_id, role="assistant", content="The calculated rebate is 7,500."
        ),
    ]
    binding = ReferenceBinding(
        kind=BindingKind.SCENARIO_PARAMETER,
        active_value="7,500",
        origin=BindingOrigin.USER_ADOPTED_ASSISTANT,
        references=[
            _assistant_ref(assistant_id, "7,500"),
            _user_ref(user_id, "Use that amount"),
        ],
    )
    resolution = TurnResolution(
        outcome=TurnOutcome.RESOLVED,
        relation=TurnRelation.FOLLOW_UP,
        effective_question="What rebate applies to eligible investment of 7,500?",
        active_bindings=[binding],
    )
    validated = validate_turn_resolution(
        resolution,
        _payload(current_id=user_id, history=history),
    )
    assert referenced_message_ids(validated.active_bindings) == (assistant_id, user_id)
    assert parameter_values_match(validated.active_bindings[0].active_value, "7500")


def test_adoption_rejects_assistant_only_mention() -> None:
    assistant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    binding = ReferenceBinding(
        kind=BindingKind.SCENARIO_PARAMETER,
        active_value="7500",
        origin=BindingOrigin.USER_ADOPTED_ASSISTANT,
        references=[_assistant_ref(assistant_id)],
    )
    resolution = TurnResolution(
        outcome=TurnOutcome.RESOLVED,
        relation=TurnRelation.FOLLOW_UP,
        effective_question="What rebate applies to 7500?",
        active_bindings=[binding],
    )
    with pytest.raises(TurnResolutionError, match="user adoption instruction"):
        validate_turn_resolution(
            resolution,
            _payload(
                current_id=user_id,
                history=[
                    HistoryMessage(
                        id=assistant_id,
                        role="assistant",
                        content="The calculated rebate is 7,500.",
                    )
                ],
            ),
        )


def test_parameter_correction_replaces_prior_active_amount() -> None:
    first = uuid.uuid4()
    second = uuid.uuid4()
    existing = [
        ReferenceBinding(
            kind=BindingKind.SCENARIO_PARAMETER,
            active_value="75,000",
            origin=BindingOrigin.USER_LITERAL,
            references=[_user_ref(first)],
        ),
        ReferenceBinding(
            kind=BindingKind.SOURCE,
            active_value="2026 Finance Act",
            origin=BindingOrigin.USER_LITERAL,
            references=[_user_ref(first)],
        ),
    ]
    incoming = ReferenceBinding(
        kind=BindingKind.SCENARIO_PARAMETER,
        active_value="90,000",
        origin=BindingOrigin.USER_LITERAL,
        references=[_user_ref(second, "90,000, not 75,000")],
    )
    replaced = replace_active_parameter(existing, incoming)
    amounts = [
        binding.active_value
        for binding in replaced
        if binding.kind is BindingKind.SCENARIO_PARAMETER
    ]
    assert amounts == ["90,000"]
    assert any(binding.kind is BindingKind.SOURCE for binding in replaced)


def test_topic_change_drops_old_amount_and_date_bindings() -> None:
    incoming = [
        ReferenceBinding(
            kind=BindingKind.TOPIC_ENTITY,
            active_value="source-tax rate",
            origin=BindingOrigin.USER_LITERAL,
            references=[_user_ref(uuid.uuid4())],
        )
    ]
    assert bindings_after_topic_change(incoming) == incoming


def test_unicode_parameter_normalization_preserves_display_text() -> None:
    bangla_grouped = "\u09ed\u09eb,\u09e6\u09e6\u09e6"
    bangla_plain = "\u09ed\u09eb\u09e6\u09e6"
    assert normalize_parameter_value(bangla_grouped) == "75000"
    assert parameter_values_match("7,500", bangla_plain)
    assert not parameter_values_match("BDT 7,500", "USD 7,500")
    binding = ReferenceBinding(
        kind=BindingKind.SCENARIO_PARAMETER,
        active_value=bangla_grouped,
        origin=BindingOrigin.USER_LITERAL,
        references=[_user_ref(uuid.uuid4())],
    )
    assert binding.active_value == bangla_grouped


@pytest.mark.parametrize(
    ("kind", "anchor", "expected_date", "origin"),
    [
        (TemporalIntentKind.TODAY, None, date(2026, 8, 1), "today"),
        (TemporalIntentKind.YESTERDAY, None, date(2026, 7, 31), "yesterday"),
        (
            TemporalIntentKind.DAY_BEFORE_IDENTIFIED_DATE,
            date(2026, 8, 1),
            date(2026, 7, 31),
            "day_before",
        ),
        (TemporalIntentKind.EXACT_DATE, date(2026, 8, 1), date(2026, 8, 1), "user_literal"),
    ],
)
def test_snapshot_dates_are_utc_midnight(
    kind: TemporalIntentKind,
    anchor: date | None,
    expected_date: date,
    origin: str,
) -> None:
    current_id = uuid.uuid4()
    bindings = []
    snapshot_origin = None
    if kind in {TemporalIntentKind.EXACT_DATE, TemporalIntentKind.DAY_BEFORE_IDENTIFIED_DATE}:
        bindings = [
            ReferenceBinding(
                kind=BindingKind.PERIOD_DATE,
                active_value="2026-08-01",
                origin=BindingOrigin.USER_LITERAL,
                references=[_user_ref(current_id)],
            )
        ]
        snapshot_origin = "user_literal"
    snapshot = resolve_effective_as_of(
        request_as_of=None,
        temporal_intent=TemporalIntent(
            kind=kind,
            anchor_date=anchor,
            requires_snapshot=True,
            snapshot_origin=snapshot_origin,
        ),
        bindings=bindings,
        reference_time=_REFERENCE,
    )
    assert snapshot.clarify is False
    assert snapshot.as_of == utc_midnight(expected_date)
    assert snapshot.origin.value == origin
    assert snapshot.suppress_web is True


def test_leap_day_and_month_boundary_subtraction() -> None:
    assert calendar_day_before(date(2024, 3, 1)) == date(2024, 2, 29)
    assert calendar_day_before(date(2025, 3, 1)) == date(2025, 2, 28)
    leap = resolve_effective_as_of(
        request_as_of=None,
        temporal_intent=TemporalIntent(
            kind=TemporalIntentKind.YESTERDAY,
            requires_snapshot=True,
        ),
        bindings=[],
        reference_time=datetime(2024, 3, 1, tzinfo=UTC),
    )
    non_leap = resolve_effective_as_of(
        request_as_of=None,
        temporal_intent=TemporalIntent(
            kind=TemporalIntentKind.YESTERDAY,
            requires_snapshot=True,
        ),
        bindings=[],
        reference_time=datetime(2025, 3, 1, tzinfo=UTC),
    )
    assert leap.as_of == utc_midnight(date(2024, 2, 29))
    assert non_leap.as_of == utc_midnight(date(2025, 2, 28))


def test_same_interpretation_when_host_date_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.modules.conversations.turn_resolution.datetime",
        datetime,
    )
    host_now = datetime(2027, 9, 5, tzinfo=UTC)

    class _HostDateTime(datetime):
        @classmethod
        def now(cls, tz: object | None = None) -> datetime:
            return host_now if tz else host_now.replace(tzinfo=None)

    monkeypatch.setattr(
        "app.modules.conversations.turn_resolution.datetime",
        _HostDateTime,
    )
    assert utc_reference_datetime() == host_now
    snapshot = resolve_effective_as_of(
        request_as_of=None,
        temporal_intent=TemporalIntent(kind=TemporalIntentKind.TODAY, requires_snapshot=True),
        bindings=[],
        reference_time=_REFERENCE,
    )
    assert snapshot.as_of == utc_midnight(date(2026, 8, 1))


def test_conflicting_request_as_of_clarifies_and_does_not_replace() -> None:
    current_id = uuid.uuid4()
    snapshot = resolve_effective_as_of(
        request_as_of=datetime(2024, 1, 1, tzinfo=UTC),
        temporal_intent=TemporalIntent(
            kind=TemporalIntentKind.EXACT_DATE,
            anchor_date=date(2026, 8, 1),
            requires_snapshot=True,
            snapshot_origin="user_literal",
        ),
        bindings=[
            ReferenceBinding(
                kind=BindingKind.PERIOD_DATE,
                active_value="2026-08-01",
                origin=BindingOrigin.USER_LITERAL,
                references=[_user_ref(current_id)],
            )
        ],
        reference_time=_REFERENCE,
    )
    assert snapshot.clarify is True
    assert snapshot.as_of is None


def test_citation_date_without_adoption_cannot_authorize_cutoff() -> None:
    assistant_id = uuid.uuid4()
    snapshot = resolve_effective_as_of(
        request_as_of=None,
        temporal_intent=TemporalIntent(
            kind=TemporalIntentKind.EXACT_DATE,
            anchor_date=date(2026, 7, 1),
            requires_snapshot=True,
        ),
        bindings=[
            ReferenceBinding(
                kind=BindingKind.PERIOD_DATE,
                active_value="2026-07-01",
                origin=BindingOrigin.CITATION_REFERENCE,
                references=[
                    BindingReference(
                        message_id=assistant_id,
                        role="assistant",
                        field="citation",
                        citation_field="source_published_date",
                    )
                ],
            )
        ],
        reference_time=_REFERENCE,
    )
    assert snapshot.clarify is True
    assert snapshot.as_of is None


def test_unbounded_before_that_clarifies_instead_of_year_end() -> None:
    snapshot = resolve_effective_as_of(
        request_as_of=None,
        temporal_intent=TemporalIntent(
            kind=TemporalIntentKind.UNBOUNDED,
            requires_snapshot=True,
        ),
        bindings=[],
        reference_time=_REFERENCE,
    )
    assert snapshot == EffectiveSnapshot(
        as_of=None,
        origin=None,
        suppress_web=False,
        clarify=True,
        clarify_reason="Unbounded temporal expression cannot authorize a snapshot cutoff.",
    )


def test_omitted_as_of_is_not_injected_for_non_snapshot_current_questions() -> None:
    inputs = effective_retrieval_inputs(
        original_message="What is the current rebate rate?",
        resolution=TurnResolution(
            outcome=TurnOutcome.RESOLVED,
            relation=TurnRelation.FOLLOW_UP,
            effective_question="What is the current investment rebate rate?",
        ),
        request_filters=RequestFilters(),
        snapshot=resolve_effective_as_of(
            request_as_of=None,
            temporal_intent=TemporalIntent(kind=TemporalIntentKind.NONE),
            bindings=[],
            reference_time=_REFERENCE,
        ),
    )
    assert inputs.as_of is None
    assert inputs.metadata_filter == {}
    assert inputs.document_id is None


def test_request_filters_stay_unchanged_on_effective_inputs() -> None:
    document_id = uuid.uuid4()
    filters = RequestFilters(
        document_id=document_id,
        metadata_filter={"source": "tax_2023"},
        as_of=datetime(2024, 1, 1, tzinfo=UTC),
    )
    inputs = effective_retrieval_inputs(
        original_message="And the threshold?",
        resolution=TurnResolution(
            outcome=TurnOutcome.RESOLVED,
            relation=TurnRelation.FOLLOW_UP,
            effective_question="What is the individual tax-free threshold?",
        ),
        request_filters=filters,
        snapshot=EffectiveSnapshot(as_of=utc_midnight(date(2026, 8, 1)), suppress_web=True),
    )
    assert inputs.document_id == document_id
    assert inputs.metadata_filter == {"source": "tax_2023"}
    assert inputs.as_of == filters.as_of


def test_unknown_message_reference_is_rejected() -> None:
    resolution = TurnResolution(
        outcome=TurnOutcome.RESOLVED,
        relation=TurnRelation.FOLLOW_UP,
        effective_question="What rebate applies to 7500?",
        active_bindings=[
            ReferenceBinding(
                kind=BindingKind.SCENARIO_PARAMETER,
                active_value="7500",
                origin=BindingOrigin.USER_LITERAL,
                references=[_user_ref(uuid.uuid4())],
            )
        ],
    )
    with pytest.raises(TurnResolutionError, match="not in the supplied history"):
        validate_turn_resolution(resolution, _payload(current_id=uuid.uuid4()))


def test_extra_resolver_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        TurnResolution.model_validate(
            {
                "outcome": "resolved",
                "relation": "follow_up",
                "effective_question": "What rebate applies?",
                "confidence": 0.9,
            }
        )


def test_iso_calendar_dates_reject_year_only_and_ambiguous_text() -> None:
    assert parse_iso_calendar_date("2026-08-01") == date(2026, 8, 1)
    with pytest.raises(TurnResolutionError):
        parse_iso_calendar_date("2026")
    with pytest.raises(TurnResolutionError):
        parse_iso_calendar_date("August 2026")


def test_parse_resolver_json_rejects_non_json_and_extra_fields() -> None:
    parsed = parse_resolver_json(
        '{"outcome":"standalone","relation":"standalone","effective_question":"What applies?"}'
    )
    assert parsed.outcome is TurnOutcome.STANDALONE
    with pytest.raises(TurnResolutionError, match="not valid JSON") as malformed:
        parse_resolver_json("not json")
    assert malformed.value.code == "malformed_json"
    with pytest.raises(TurnResolutionError, match="schema") as schema:
        parse_resolver_json(
            '{"outcome":"resolved","relation":"follow_up","effective_question":"Q","confidence":0.9}'
        )
    assert schema.value.code == "schema_mismatch"
    assert schema.value.field == "confidence"


def test_parse_accepts_unbounded_clarify_json_without_retry() -> None:
    parsed = parse_resolver_json(
        """
        {
          "outcome": "clarify",
          "relation": "follow_up",
          "effective_question": "Which date do you mean?",
          "active_bindings": [],
          "temporal_intent": "unbounded",
          "clarification_question": "",
          "reason": ""
        }
        """
    )
    assert parsed.outcome is TurnOutcome.CLARIFY
    assert parsed.temporal_intent.kind is TemporalIntentKind.UNBOUNDED
    assert parsed.clarification_question == "Which date do you mean?"
    assert parsed.reason is None


def test_parse_empty_anchor_date_is_omitted() -> None:
    parsed = parse_resolver_json(
        """
        {
          "outcome": "clarify",
          "relation": "follow_up",
          "effective_question": "Which date?",
          "temporal_intent": {
            "kind": "unbounded",
            "anchor_date": "",
            "requires_snapshot": true,
            "snapshot_origin": ""
          },
          "clarification_question": "Which date?"
        }
        """
    )
    assert parsed.temporal_intent.anchor_date is None
    assert parsed.temporal_intent.snapshot_origin is None
    assert parsed.temporal_intent.requires_snapshot is True


def test_parse_null_bindings_and_numeric_active_value() -> None:
    parsed = parse_resolver_json(
        """
        {
          "outcome": "resolved",
          "relation": "correction",
          "effective_question": "What rebate applies to 75000?",
          "active_bindings": [
            {
              "kind": "scenario_parameter",
              "active_value": 75000,
              "origin": "user_literal",
              "references": [
                {
                  "message_id": "11111111-1111-1111-1111-111111111111",
                  "role": "user",
                  "excerpt": "75000"
                }
              ]
            }
          ],
          "temporal_intent": null,
          "clarification_question": null,
          "reason": null
        }
        """
    )
    assert parsed.active_bindings[0].active_value == "75000"
    assert parsed.temporal_intent.kind is TemporalIntentKind.NONE


def test_bound_resolution_history_drops_oldest_complete_messages() -> None:
    messages = [HistoryMessage(id=uuid.uuid4(), role="user", content="a" * 80) for _ in range(3)]
    bounded, truncated = bound_resolution_history(messages, max_messages=8, max_chars=100)
    assert truncated is True
    assert len(bounded) == 1
    assert bounded[0].content == messages[-1].content
    capped, message_truncated = bound_resolution_history(messages, max_messages=2)
    assert message_truncated is True
    assert [item.content for item in capped] == [messages[-2].content, messages[-1].content]
