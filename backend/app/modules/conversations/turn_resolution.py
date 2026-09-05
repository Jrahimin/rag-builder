"""Bounded turn-resolution contracts and deterministic validation helpers.

Immutable models, JSON parsing, history bounding, and calendar/binding checks live
here. Production chat invokes one ``TurnResolver`` call; Journey patches
``utc_reference_datetime`` on this module.
"""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

TURN_RESOLUTION_VERSION = "v1"
RESOLUTION_HISTORY_MESSAGE_CAP = 8
RESOLUTION_HISTORY_CHAR_BUDGET = 16_000
RESOLUTION_TIMEOUT_SECONDS = 10.0
RESOLUTION_MAX_OUTPUT_TOKENS = 2048

_GROUPING_CHARACTERS = frozenset({",", "_", " ", "\u00a0", "\u202f", "\u09f7", "\u066c"})


def utc_reference_datetime() -> datetime:
    """UTC instant used for conversational date interpretation.

    Journey patches this function on the module. Production chat looks it up at
    call time so the same frozen clock applies.
    """
    return datetime.now(UTC)


def utc_reference_date() -> date:
    """Calendar date of :func:`utc_reference_datetime`."""
    return utc_reference_datetime().date()


def utc_midnight(value: date) -> datetime:
    """Construct an exclusive UTC-midnight snapshot instant from a calendar date."""
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def calendar_day_before(value: date) -> date:
    """Return the calendar day immediately before ``value``, including leap days."""
    return value - timedelta(days=1)


def normalize_parameter_value(value: str) -> str:
    """Normalize Unicode digits and grouping separators while remaining case-insensitive.

    Display text on bindings stays untouched; this form is only for deterministic
    comparison of amounts, rates, and ISO dates.
    """
    characters: list[str] = []
    for char in unicodedata.normalize("NFKC", value).strip():
        try:
            characters.append(str(unicodedata.digit(char)))
        except (TypeError, ValueError):
            if char in _GROUPING_CHARACTERS:
                continue
            characters.append({"\u2212": "-", "\u066b": "."}.get(char, char))
    return "".join(characters).casefold()


def parameter_values_match(left: str, right: str) -> bool:
    """True when two parameter display strings name the same normalized value."""
    return normalize_parameter_value(left) == normalize_parameter_value(right)


def _literal_value_present(value: str, text: str) -> bool:
    """Match display values without changing signs, decimals, currencies or units."""
    normalized = normalize_parameter_value(value)
    if not normalized:
        return False
    # Alphabetic context can be translated in the effective question. Bindings
    # themselves retain source display text. Numeric boundaries prevent 10 from
    # matching -10, 100, 10.5 or 10%.
    if not any(c.isdigit() for c in normalized):
        literal = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
        content = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
        return re.search(rf"(?<!\w){re.escape(literal)}(?!\w)", content) is not None
    return (
        re.search(
            rf"(?<![\d.+%\-]){re.escape(normalized)}(?!\d|%|\.\d)",
            normalize_parameter_value(text),
        )
        is not None
    )


class TurnResolutionError(ValueError):
    """Deterministic rejection of a resolver interpretation."""


class BindingKind(StrEnum):
    TOPIC_ENTITY = "topic_entity"
    SCENARIO_PARAMETER = "scenario_parameter"
    PERIOD_DATE = "period_date"
    SOURCE = "source"


class BindingOrigin(StrEnum):
    USER_LITERAL = "user_literal"
    USER_ADOPTED_ASSISTANT = "user_adopted_assistant"
    ASSISTANT_REFERENCE = "assistant_reference"
    CITATION_REFERENCE = "citation_reference"


class TurnOutcome(StrEnum):
    STANDALONE = "standalone"
    RESOLVED = "resolved"
    CLARIFY = "clarify"
    FALLBACK = "fallback"


class TurnRelation(StrEnum):
    STANDALONE = "standalone"
    FOLLOW_UP = "follow_up"
    CORRECTION = "correction"
    TOPIC_CHANGE = "topic_change"


class TemporalIntentKind(StrEnum):
    NONE = "none"
    TODAY = "today"
    YESTERDAY = "yesterday"
    DAY_BEFORE_IDENTIFIED_DATE = "day_before_identified_date"
    EXACT_DATE = "exact_date"
    UNBOUNDED = "unbounded"


class SnapshotOrigin(StrEnum):
    REQUEST_AS_OF = "request_as_of"
    USER_LITERAL = "user_literal"
    USER_ADOPTED = "user_adopted"
    TODAY = "today"
    YESTERDAY = "yesterday"
    DAY_BEFORE = "day_before"


class BindingReference(BaseModel):
    """A pointer back to supplied history text or citation identity/date fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: uuid.UUID
    role: Literal["user", "assistant"]
    field: Literal["content", "citation"] = "content"
    citation_field: str | None = None
    excerpt: str | None = None


class CitationIdentity(BaseModel):
    """Citation identity and date metadata supplied to the resolver; never evidence text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: uuid.UUID
    document_id: uuid.UUID | None = None
    filename: str | None = None
    source_title: str | None = None
    source_published_date: date | None = None
    source_effective_from: date | None = None
    source_effective_to: date | None = None


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    citations: list[CitationIdentity] = Field(default_factory=list)


class RequestFilters(BaseModel):
    """Per-request scope. Omitted previous API filters never become sticky."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: uuid.UUID | None = None
    metadata_filter: dict[str, str] = Field(default_factory=dict)
    as_of: datetime | None = None


class ReferenceBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: BindingKind
    active_value: str = Field(min_length=1)
    origin: BindingOrigin
    references: list[BindingReference] = Field(min_length=1)


class TemporalIntent(BaseModel):
    """Model-facing temporal interpretation. Calendar arithmetic stays in code."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: TemporalIntentKind = TemporalIntentKind.NONE
    anchor_date: date | None = None
    requires_snapshot: bool = False
    snapshot_origin: SnapshotOrigin | None = None

    @model_validator(mode="after")
    def validate_anchor(self) -> TemporalIntent:
        if self.kind is TemporalIntentKind.DAY_BEFORE_IDENTIFIED_DATE and self.anchor_date is None:
            raise ValueError("day_before_identified_date requires an identified exact anchor_date")
        if self.kind is TemporalIntentKind.EXACT_DATE and self.anchor_date is None:
            raise ValueError("exact_date requires an identified exact anchor_date")
        if self.kind is TemporalIntentKind.NONE and self.requires_snapshot:
            raise ValueError("temporal intent none cannot require a snapshot")
        return self


class TurnResolutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current_message_id: uuid.UUID
    current_message: str
    history: list[HistoryMessage] = Field(default_factory=list)
    citation_metadata: list[CitationIdentity] = Field(default_factory=list)
    request_filters: RequestFilters = Field(default_factory=RequestFilters)
    reference_time: datetime


class TurnResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: TurnOutcome
    relation: TurnRelation
    effective_question: str = Field(min_length=1)
    active_bindings: list[ReferenceBinding] = Field(default_factory=list)
    temporal_intent: TemporalIntent = Field(default_factory=TemporalIntent)
    clarification_question: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_outcome_shape(self) -> TurnResolution:
        if self.outcome is TurnOutcome.CLARIFY and not (
            self.clarification_question and self.clarification_question.strip()
        ):
            raise ValueError("clarify outcomes require a user-facing clarification question")
        if self.outcome is TurnOutcome.STANDALONE and self.relation is not TurnRelation.STANDALONE:
            raise ValueError("standalone outcomes must use the standalone relation")
        if self.outcome is TurnOutcome.RESOLVED and self.relation is TurnRelation.STANDALONE:
            raise ValueError("resolved outcomes cannot use the standalone relation")
        return self


class EffectiveSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: datetime | None = None
    origin: SnapshotOrigin | None = None
    suppress_web: bool = False
    clarify: bool = False
    clarify_reason: str | None = None


class EffectiveRetrievalInputs(BaseModel):
    """Application-constructed retrieval inputs. Filters stay exactly as requested."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str
    document_id: uuid.UUID | None = None
    metadata_filter: dict[str, str] = Field(default_factory=dict)
    as_of: datetime | None = None
    suppress_web: bool = False


def parse_iso_calendar_date(value: str) -> date:
    """Accept only an unambiguous ``YYYY-MM-DD`` calendar date."""
    normalized = unicodedata.normalize("NFKC", value).strip()
    digits: list[str] = []
    for char in normalized:
        try:
            digits.append(str(unicodedata.digit(char)))
        except (TypeError, ValueError):
            digits.append(char)
    text = "".join(digits)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise TurnResolutionError(
            f"Value {value!r} is not an unambiguous ISO calendar date."
        ) from exc
    if parsed.isoformat() != text:
        raise TurnResolutionError(f"Value {value!r} is not an unambiguous ISO calendar date.")
    return parsed


def referenced_message_ids(bindings: Sequence[ReferenceBinding]) -> tuple[uuid.UUID, ...]:
    """Stable message identities derived from accepted bindings."""
    seen: dict[uuid.UUID, None] = {}
    for binding in bindings:
        for reference in binding.references:
            seen.setdefault(reference.message_id, None)
    return tuple(seen)


def replace_active_parameter(
    bindings: Sequence[ReferenceBinding],
    incoming: ReferenceBinding,
) -> list[ReferenceBinding]:
    """Latest explicit scenario-parameter correction replaces the prior active amount."""
    if incoming.kind is not BindingKind.SCENARIO_PARAMETER:
        return [*bindings, incoming]
    retained = [
        binding for binding in bindings if binding.kind is not BindingKind.SCENARIO_PARAMETER
    ]
    return [*retained, incoming]


def bindings_after_topic_change(
    incoming: Sequence[ReferenceBinding],
) -> list[ReferenceBinding]:
    """Drop old topic-specific bindings and conversational dates; keep only incoming ones."""
    return list(incoming)


def validate_turn_resolution(
    resolution: TurnResolution,
    payload: TurnResolutionInput,
) -> TurnResolution:
    """Reject extra semantics that are not allowed to survive into retrieval."""
    if payload.reference_time.tzinfo is None:
        raise TurnResolutionError("reference_time must be timezone-aware UTC.")
    known_messages = _known_messages(payload)
    for binding in resolution.active_bindings:
        _validate_binding(binding, payload=payload, known_messages=known_messages)
    if resolution.temporal_intent.kind is TemporalIntentKind.UNBOUNDED:
        if resolution.temporal_intent.requires_snapshot:
            if resolution.outcome is not TurnOutcome.CLARIFY:
                raise TurnResolutionError(
                    "Unbounded temporal expressions cannot authorize a snapshot; clarify instead."
                )
        elif resolution.outcome is TurnOutcome.RESOLVED and resolution.temporal_intent.anchor_date:
            raise TurnResolutionError(
                "Unbounded temporal expressions cannot carry an exact snapshot date."
            )
    return resolution


def resolve_effective_as_of(
    *,
    request_as_of: datetime | None,
    temporal_intent: TemporalIntent,
    bindings: Sequence[ReferenceBinding],
    reference_time: datetime,
) -> EffectiveSnapshot:
    """Convert validated temporal intent into a UTC snapshot without replacing request as_of."""
    if reference_time.tzinfo is None:
        raise TurnResolutionError("reference_time must be timezone-aware UTC.")
    reference_day = reference_time.astimezone(UTC).date()
    derived, origin = _derived_snapshot_date(
        temporal_intent=temporal_intent,
        bindings=bindings,
        reference_day=reference_day,
    )
    if temporal_intent.kind is TemporalIntentKind.UNBOUNDED and temporal_intent.requires_snapshot:
        return EffectiveSnapshot(
            as_of=None,
            origin=None,
            suppress_web=False,
            clarify=True,
            clarify_reason="Unbounded temporal expression cannot authorize a snapshot cutoff.",
        )
    if derived is None:
        if request_as_of is not None:
            return EffectiveSnapshot(
                as_of=request_as_of,
                origin=SnapshotOrigin.REQUEST_AS_OF,
                suppress_web=True,
            )
        if temporal_intent.requires_snapshot:
            return EffectiveSnapshot(
                as_of=None,
                origin=None,
                suppress_web=False,
                clarify=True,
                clarify_reason="Snapshot request is missing a user-supplied or adopted exact date.",
            )
        return EffectiveSnapshot()
    derived_instant = utc_midnight(derived)
    if request_as_of is not None:
        if request_as_of.astimezone(UTC) != derived_instant:
            return EffectiveSnapshot(
                as_of=None,
                origin=None,
                suppress_web=False,
                clarify=True,
                clarify_reason="Interpreted snapshot conflicts with the supplied request as_of.",
            )
        return EffectiveSnapshot(
            as_of=request_as_of,
            origin=SnapshotOrigin.REQUEST_AS_OF,
            suppress_web=True,
        )
    return EffectiveSnapshot(
        as_of=derived_instant,
        origin=origin,
        suppress_web=True,
    )


def effective_retrieval_inputs(
    *,
    original_message: str,
    resolution: TurnResolution,
    request_filters: RequestFilters,
    snapshot: EffectiveSnapshot,
) -> EffectiveRetrievalInputs:
    """Build retrieval inputs. Document and metadata filters never change here."""
    query = (
        resolution.effective_question
        if resolution.outcome is TurnOutcome.RESOLVED
        else original_message
    )
    as_of = request_filters.as_of
    if as_of is None and snapshot.as_of is not None and not snapshot.clarify:
        as_of = snapshot.as_of
    return EffectiveRetrievalInputs(
        query=query,
        document_id=request_filters.document_id,
        metadata_filter=dict(request_filters.metadata_filter),
        as_of=as_of,
        suppress_web=bool(
            snapshot.suppress_web
            or request_filters.document_id
            or request_filters.metadata_filter
            or request_filters.as_of
        ),
    )


def _known_messages(payload: TurnResolutionInput) -> dict[uuid.UUID, HistoryMessage | None]:
    messages: dict[uuid.UUID, HistoryMessage | None] = {
        payload.current_message_id: None,
    }
    for item in payload.history:
        messages[item.id] = item
    return messages


def _validate_binding(
    binding: ReferenceBinding,
    *,
    payload: TurnResolutionInput,
    known_messages: dict[uuid.UUID, HistoryMessage | None],
) -> None:
    roles = []
    value_references: list[BindingReference] = []
    for reference in binding.references:
        if reference.message_id not in known_messages:
            raise TurnResolutionError(
                f"Binding reference {reference.message_id} is not in the supplied history."
            )
        history_item = known_messages[reference.message_id]
        expected_role = "user" if history_item is None else history_item.role
        if reference.role != expected_role:
            raise TurnResolutionError(
                f"Binding reference {reference.message_id} has role {reference.role}, "
                f"expected {expected_role}."
            )
        roles.append(reference.role)
        if reference.field == "citation" and not reference.citation_field:
            raise TurnResolutionError("Citation references require a citation_field.")
        if (
            reference.excerpt
            and history_item is not None
            and reference.field == "content"
            and reference.excerpt not in history_item.content
        ):
            raise TurnResolutionError("Binding excerpt is not present in the referenced message.")
        if (
            reference.excerpt
            and history_item is None
            and reference.field == "content"
            and reference.excerpt not in payload.current_message
        ):
            raise TurnResolutionError("Binding excerpt is not present in the current message.")
        if reference.field == "citation":
            allowed = set(CitationIdentity.model_fields) - {"message_id"}
            if reference.role != "assistant" or reference.citation_field not in allowed:
                raise TurnResolutionError("Invalid citation identity/date field.")
            values = [
                str(getattr(item, reference.citation_field))
                for item in payload.citation_metadata
                if item.message_id == reference.message_id
                and getattr(item, reference.citation_field) is not None
            ]
            if reference.excerpt is not None:
                values = [value for value in values if reference.excerpt == value]
            if not values:
                raise TurnResolutionError("Citation field is not in the supplied metadata.")
        else:
            content = payload.current_message if history_item is None else history_item.content
            values = [reference.excerpt or content]
        if any(_literal_value_present(binding.active_value, value) for value in values):
            value_references.append(reference)
    unique_roles = set(roles)
    if binding.origin is BindingOrigin.USER_LITERAL:
        if unique_roles - {"user"}:
            raise TurnResolutionError("user_literal bindings may only reference user messages.")
    elif binding.origin is BindingOrigin.ASSISTANT_REFERENCE:
        if unique_roles - {"assistant"}:
            raise TurnResolutionError(
                "assistant_reference bindings may only identify assistant messages."
            )
    elif binding.origin is BindingOrigin.USER_ADOPTED_ASSISTANT:
        if "assistant" not in unique_roles or "user" not in unique_roles:
            raise TurnResolutionError(
                "user_adopted_assistant bindings require both the assistant value "
                "and the user adoption instruction."
            )
    elif binding.origin is BindingOrigin.CITATION_REFERENCE and any(
        reference.field != "citation" for reference in binding.references
    ):
        raise TurnResolutionError("citation_reference bindings may only cite citation fields.")

    if binding.origin is BindingOrigin.USER_ADOPTED_ASSISTANT:
        positions = {item.id: index for index, item in enumerate(payload.history)}
        positions[payload.current_message_id] = len(payload.history)
        if not any(
            value.role == "assistant"
            and adoption.role == "user"
            and adoption.field == "content"
            and bool(adoption.excerpt and adoption.excerpt.strip())
            and positions[adoption.message_id] > positions[value.message_id]
            for value in value_references
            for adoption in binding.references
        ):
            raise TurnResolutionError(
                "Adoption requires a referenced value and later user instruction."
            )
    elif not value_references:
        raise TurnResolutionError("Active value is not present in its referenced text or metadata.")


def _derived_snapshot_date(
    *,
    temporal_intent: TemporalIntent,
    bindings: Sequence[ReferenceBinding],
    reference_day: date,
) -> tuple[date | None, SnapshotOrigin | None]:
    if not temporal_intent.requires_snapshot:
        return None, None
    if temporal_intent.kind is TemporalIntentKind.TODAY:
        return reference_day, SnapshotOrigin.TODAY
    if temporal_intent.kind is TemporalIntentKind.YESTERDAY:
        return calendar_day_before(reference_day), SnapshotOrigin.YESTERDAY
    if temporal_intent.kind in {
        TemporalIntentKind.DAY_BEFORE_IDENTIFIED_DATE,
        TemporalIntentKind.EXACT_DATE,
    }:
        # Model-supplied origin is descriptive only. Authority comes from the
        # validated binding whose literal date matches this exact anchor.
        origin = None
        for binding in bindings:
            if binding.kind is not BindingKind.PERIOD_DATE or binding.origin not in {
                BindingOrigin.USER_LITERAL,
                BindingOrigin.USER_ADOPTED_ASSISTANT,
            }:
                continue
            try:
                anchor = parse_iso_calendar_date(binding.active_value)
            except TurnResolutionError:
                continue
            if anchor == temporal_intent.anchor_date:
                origin = (
                    SnapshotOrigin.USER_LITERAL
                    if binding.origin is BindingOrigin.USER_LITERAL
                    else SnapshotOrigin.USER_ADOPTED
                )
                break
        if origin is None or temporal_intent.anchor_date is None:
            return None, None
        if temporal_intent.kind is TemporalIntentKind.DAY_BEFORE_IDENTIFIED_DATE:
            try:
                return calendar_day_before(temporal_intent.anchor_date), SnapshotOrigin.DAY_BEFORE
            except OverflowError as exc:
                raise TurnResolutionError(
                    "Snapshot date is outside the supported calendar."
                ) from exc
        return temporal_intent.anchor_date, origin
    return None, None


def resolution_from_mapping(payload: MappingLike) -> TurnResolution:
    """Parse strict resolver JSON without a repair loop."""
    return TurnResolution.model_validate(payload)


def parse_resolver_json(text: str) -> TurnResolution:
    """Parse raw resolver output as strict JSON. No fence stripping or repair."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TurnResolutionError("Resolver output is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise TurnResolutionError("Resolver output must be a JSON object.")
    try:
        return TurnResolution.model_validate(payload)
    except ValidationError as exc:
        raise TurnResolutionError("Resolver output does not match the resolution schema.") from exc


def history_char_size(messages: Sequence[HistoryMessage]) -> int:
    """Count characters of message text plus citation identity/date metadata."""
    total = 0
    for message in messages:
        total += len(message.content)
        for citation in message.citations:
            total += len(str(citation.document_id or ""))
            total += len(citation.filename or "")
            total += len(citation.source_title or "")
            if citation.source_published_date is not None:
                total += len(citation.source_published_date.isoformat())
            if citation.source_effective_from is not None:
                total += len(citation.source_effective_from.isoformat())
            if citation.source_effective_to is not None:
                total += len(citation.source_effective_to.isoformat())
    return total


def bound_resolution_history(
    messages: Sequence[HistoryMessage],
    *,
    max_messages: int,
    max_chars: int = RESOLUTION_HISTORY_CHAR_BUDGET,
) -> tuple[list[HistoryMessage], bool]:
    """Keep the newest complete messages within the resolver message and char caps."""
    cap = min(max(max_messages, 0), RESOLUTION_HISTORY_MESSAGE_CAP)
    retained = list(messages[-cap:]) if cap else []
    truncated = len(messages) > len(retained)
    while retained and history_char_size(retained) > max_chars:
        retained = retained[1:]
        truncated = True
    return retained, truncated


def fallback_resolution(original_message: str) -> TurnResolution:
    """Discard interpretation and keep the raw current message."""
    return TurnResolution(
        outcome=TurnOutcome.FALLBACK,
        relation=TurnRelation.STANDALONE,
        effective_question=original_message,
        reason="fallback",
    )


MappingLike = dict[str, Any] | TurnResolution
