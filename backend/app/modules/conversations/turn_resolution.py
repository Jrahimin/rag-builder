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


def parameter_bindings_match(left: str, right: str) -> bool:
    """Match binding values, allowing a currency or unit on only one side.

    ``BDT 6,000`` matches ``6000``. Conflicting affixes such as BDT vs USD do not.
    """
    if parameter_values_match(left, right):
        return True
    left_core, left_affix = _split_quantity_token(left)
    right_core, right_affix = _split_quantity_token(right)
    if not parameter_values_match(left_core, right_core):
        return False
    if left_affix and right_affix:
        return parameter_values_match(left_affix, right_affix)
    return True


def _value_has_letter(value: str) -> bool:
    return _LETTER_RE.search(unicodedata.normalize("NFKC", value)) is not None


def _literal_value_present(value: str, text: str) -> bool:
    """Match display values without changing signs, decimals, currencies or units.

    An amount may keep a conversation currency that the current span omitted
    (``BDT 90,000`` in ``90,000``). A conflicting currency in the same span is
    still rejected. Source phrases keep their spaces (``2023 Act``).
    """
    if _literal_core_present(value, text):
        return not _quantity_affix_conflicts(value, text)
    core, _affix = _split_quantity_token(value)
    if core != value and _literal_core_present(core, text):
        return not _quantity_affix_conflicts(value, text)
    return False


def _literal_core_present(value: str, text: str) -> bool:
    """Match a source phrase, or a numeric core with sign/decimal/percent boundaries."""
    folded_value = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    folded_text = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    if folded_value and (
        _value_has_letter(value) or not any(char.isdigit() for char in folded_value)
    ):
        return re.search(rf"(?<!\w){re.escape(folded_value)}(?!\w)", folded_text) is not None
    normalized = normalize_parameter_value(value)
    if not normalized or not any(char.isdigit() for char in normalized):
        return False
    return (
        re.search(
            rf"(?<![\d.+%\-]){re.escape(normalized)}(?!\d|%|\.\d)",
            normalize_parameter_value(text),
        )
        is not None
    )


def _quantity_affix_conflicts(value: str, text: str) -> bool:
    """True when the span attaches a different currency or unit to the same amount."""
    core, affix = _split_quantity_token(value)
    if not affix or not any(char.isdigit() for char in core):
        return False
    for token in _affixed_quantity_tokens(text):
        token_core, token_affix = _split_quantity_token(token)
        if not token_affix or not parameter_values_match(token_core, core):
            continue
        if not parameter_values_match(token_affix, affix):
            return True
    return False


class TurnResolutionError(ValueError):
    """Deterministic rejection of a resolver interpretation.

    ``code`` is a stable, Journey-visible subcode. ``field`` is an optional
    contract path. Neither stores raw rejected model output.
    """

    def __init__(self, message: str, *, code: str, field: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


_CURRENCY_SYMBOLS = frozenset({"$", "€", "£", "¥", "৳"})
_AMOUNT_GROUPING = frozenset({",", "_", "\u00a0", "\u202f", "\u09f7", "\u066c"})
_ISO_DATE_RE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_CURRENCY_TOKEN_RE = re.compile(
    r"(?<![\w$€£¥৳])(?:bdt|usd|eur|gbp|inr|tk|taka|\u099f\u09be\u0995\u09be)"
    r"(?![\w$€£¥৳])",
    re.IGNORECASE,
)
_MEASURED_QUANTITY_RE = re.compile(
    r"(?<![\d.])[+\-]?\d+(?:[,_\u00a0\u202f\u09f7\u066c]\d{2,3})*(?:[.\u066b]\d+)?"
    r"\s+(?:hours?|days?|weeks?|months?|years?|minutes?|seconds?)\b",
    re.IGNORECASE,
)
_AMOUNT_RE = re.compile(
    r"(?<![\d.])[+\-]?\d+(?:[,_\u00a0\u202f\u09f7\u066c]\d{2,3})*(?:[.\u066b]\d+)?%?"
)
_CURRENCY_OR_UNIT_AFFIX_RE = re.compile(
    r"(?:bdt|usd|eur|gbp|inr|tk|taka|\u099f\u09be\u0995\u09be|"
    r"hours?|days?|weeks?|months?|years?|minutes?|seconds?)",
    re.IGNORECASE,
)
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
_ANAPHORA_RE = re.compile(
    r"(?:"
    r"\b(?:that|this|those|the same)\s+"
    r"(?:amount|rebate|investment|figure|value|number|rate|one)\b"
    r"|\b(?:use|using|not)\s+that\b"
    r"|\u09b8\u09c7\u0987|\u0990|\u0993\u0987|\u09b8\u09c7\u099f\u09be|\u098f\u099f\u09be"
    r")",
    re.IGNORECASE,
)


def _fold_for_extraction(text: str) -> str:
    """Convert Unicode digits and signs while keeping grouping for span extraction."""
    characters: list[str] = []
    for char in unicodedata.normalize("NFKC", text):
        try:
            characters.append(str(unicodedata.digit(char)))
        except (TypeError, ValueError):
            characters.append({"\u2212": "-", "\u066b": "."}.get(char, char))
    return "".join(characters)


def _amount_span_is_parameter_like(span: str) -> bool:
    if span[:1] in "+-" or span.endswith("%"):
        return True
    if any(separator in span for separator in _AMOUNT_GROUPING):
        return True
    if "." in span:
        return True
    return len("".join(char for char in span if char.isdigit())) >= 5


def _skip_spaces(text: str, index: int, *, direction: int) -> int:
    cursor = index
    limit = len(text) if direction > 0 else -1
    while cursor != limit and text[cursor].isspace():
        cursor += direction
    return cursor


def _expand_adjacent_currency(folded: str, start: int, end: int) -> tuple[int, int]:
    """Attach a currency only when it sits next to an amount."""
    left = _skip_spaces(folded, start - 1, direction=-1)
    if left >= 0 and folded[left] in _CURRENCY_SYMBOLS:
        start = left
    else:
        prefix = None
        for match in _CURRENCY_TOKEN_RE.finditer(folded[:start]):
            prefix = match
        if prefix is not None and _skip_spaces(folded, prefix.end(), direction=1) == start:
            start = prefix.start()
    right = _skip_spaces(folded, end, direction=1)
    if right < len(folded) and folded[right] in _CURRENCY_SYMBOLS:
        end = right + 1
    else:
        suffix = _CURRENCY_TOKEN_RE.match(folded[right:]) if right < len(folded) else None
        if suffix is not None:
            end = right + suffix.end()
    return start, end


def _parameter_like_tokens(text: str) -> tuple[str, ...]:
    """Extract dates, signed/grouped amounts, percents, and measured quantities.

    Bare temporal words such as ``day``/``month``/``year`` and bare currency
    labels are not scenario quantities. Units and currencies count only when
    attached to a number.
    """
    folded = _fold_for_extraction(text)
    occupied = [False] * len(folded)
    tokens: list[str] = []

    def _take(start: int, end: int) -> None:
        if start >= end or any(occupied[start:end]):
            return
        occupied[start:end] = [True] * (end - start)
        tokens.append(folded[start:end].strip())

    for match in _ISO_DATE_RE.finditer(folded):
        _take(match.start(), match.end())
    for match in _MEASURED_QUANTITY_RE.finditer(folded):
        _take(match.start(), match.end())
    for match in _AMOUNT_RE.finditer(folded):
        if not _amount_span_is_parameter_like(match.group()):
            continue
        start, end = _expand_adjacent_currency(folded, match.start(), match.end())
        _take(start, end)
    return tuple(tokens)


def _split_quantity_token(token: str) -> tuple[str, str | None]:
    """Return the numeric/date/percent core and an optional currency or unit affix."""
    stripped = token.strip()
    if _ISO_DATE_RE.fullmatch(stripped):
        return stripped, None
    if stripped.endswith("%"):
        return stripped, None
    affix = None
    core = stripped
    prefix = _CURRENCY_OR_UNIT_AFFIX_RE.match(stripped)
    if prefix is not None:
        affix = prefix.group()
        core = stripped[prefix.end() :].lstrip(" \t")
        if core[:1] in _CURRENCY_SYMBOLS:
            affix = core[0]
            core = core[1:].lstrip(" \t")
    else:
        if stripped[:1] in _CURRENCY_SYMBOLS:
            affix = stripped[0]
            core = stripped[1:].lstrip(" \t")
        suffix = None
        for match in _CURRENCY_OR_UNIT_AFFIX_RE.finditer(core):
            suffix = match
        if suffix is not None and suffix.end() == len(core.rstrip()):
            trailing = core[suffix.start() :].strip()
            remainder = core[: suffix.start()].rstrip(" \t")
            if remainder:
                affix = trailing if affix is None else f"{affix} {trailing}"
                core = remainder
        elif core[-1:] in _CURRENCY_SYMBOLS:
            affix = core[-1] if affix is None else f"{affix} {core[-1]}"
            core = core[:-1].rstrip(" \t")
    return (core or stripped), affix


def _affixed_quantity_tokens(text: str) -> tuple[str, ...]:
    """Amounts with an attached currency or unit, including short 4-digit amounts."""
    folded = _fold_for_extraction(text)
    tokens = list(_parameter_like_tokens(text))
    for match in _AMOUNT_RE.finditer(folded):
        start, end = _expand_adjacent_currency(folded, match.start(), match.end())
        span = folded[start:end].strip()
        _core, affix = _split_quantity_token(span)
        if affix:
            tokens.append(span)
    return tuple(tokens)


def _token_justified_by_texts(token: str, texts: Sequence[str]) -> bool:
    return any(_literal_value_present(token, text) for text in texts if text)


def _validate_effective_question_parameters(
    resolution: TurnResolution,
    payload: TurnResolutionInput,
) -> None:
    """Reject resolved rewrites that invent or swap parameter-like values."""
    if resolution.outcome is not TurnOutcome.RESOLVED:
        return
    allowed_texts = [
        payload.current_message,
        *(binding.active_value for binding in resolution.active_bindings),
    ]
    history_texts = [item.content for item in payload.history]
    new_amount_texts = [
        payload.current_message,
        *(
            binding.active_value
            for binding in resolution.active_bindings
            if binding.kind is BindingKind.SCENARIO_PARAMETER
        ),
    ]
    correction_mentions_old = resolution.relation is TurnRelation.CORRECTION
    for token in _parameter_like_tokens(resolution.effective_question):
        if _token_justified_by_texts(token, allowed_texts):
            continue
        core, affix = _split_quantity_token(token)
        if (
            core != token
            and _token_justified_by_texts(core, allowed_texts)
            and (
                affix is None or _token_justified_by_texts(affix, (*allowed_texts, *history_texts))
            )
        ):
            continue
        if (
            correction_mentions_old
            and not token.endswith("%")
            and _token_justified_by_texts(token, history_texts)
            and any(
                _literal_value_present(value, resolution.effective_question)
                for value in new_amount_texts
                if any(char.isdigit() for char in value)
            )
        ):
            continue
        raise TurnResolutionError(
            "Effective question introduces or substitutes a parameter-like value.",
            code="mutated_effective_question",
            field="effective_question",
        )


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


def _blank_optional_strings(data: Any, keys: tuple[str, ...]) -> Any:
    """Treat LLM empty strings as omitted optional fields."""
    if not isinstance(data, dict):
        return data
    updated = dict(data)
    for key in keys:
        value = updated.get(key)
        if isinstance(value, str) and not value.strip():
            updated[key] = None
    return updated


def _coerce_json_bool(value: Any) -> Any:
    """Accept JSON true/false strings. Does not invent other truthy values."""
    if isinstance(value, str):
        folded = value.strip().casefold()
        if folded in {"true", "false"}:
            return folded == "true"
    return value


def _stringify_active_value(value: Any) -> Any:
    """Display bindings are strings; integer JSON amounts are the same span."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return value


def _coerce_binding_payload(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    updated = dict(item)
    if "active_value" in updated:
        updated["active_value"] = _stringify_active_value(updated["active_value"])
    return updated


class BindingReference(BaseModel):
    """A pointer back to supplied history text or citation identity/date fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: uuid.UUID
    role: Literal["user", "assistant"]
    field: Literal["content", "citation"] = "content"
    citation_field: str | None = None
    excerpt: str | None = None

    @model_validator(mode="before")
    @classmethod
    def blank_optional_fields(cls, data: Any) -> Any:
        return _blank_optional_strings(data, ("citation_field", "excerpt"))


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

    @model_validator(mode="before")
    @classmethod
    def coerce_kind_and_blanks(cls, data: Any) -> Any:
        if isinstance(data, str):
            data = {"kind": data}
        payload = _blank_optional_strings(data, ("anchor_date", "snapshot_origin"))
        if isinstance(payload, dict) and "requires_snapshot" in payload:
            payload["requires_snapshot"] = _coerce_json_bool(payload["requires_snapshot"])
        return payload

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

    @model_validator(mode="before")
    @classmethod
    def coerce_optional_json(cls, data: Any) -> Any:
        """Normalize empty optional fields. Does not call the model again."""
        if not isinstance(data, dict):
            return data
        payload = _blank_optional_strings(
            dict(data),
            ("clarification_question", "reason"),
        )
        if payload.get("active_bindings") is None:
            payload["active_bindings"] = []
        elif isinstance(payload.get("active_bindings"), list):
            payload["active_bindings"] = [
                _coerce_binding_payload(item) for item in payload["active_bindings"]
            ]
        temporal = payload.get("temporal_intent")
        if temporal is None or temporal == "":
            payload.pop("temporal_intent", None)
        elif isinstance(temporal, str) and temporal.strip():
            payload["temporal_intent"] = {"kind": temporal.strip()}
        elif isinstance(temporal, dict):
            payload["temporal_intent"] = _blank_optional_strings(
                dict(temporal),
                ("anchor_date", "snapshot_origin"),
            )
            if "requires_snapshot" in payload["temporal_intent"]:
                payload["temporal_intent"]["requires_snapshot"] = _coerce_json_bool(
                    payload["temporal_intent"]["requires_snapshot"]
                )
        if payload.get("outcome") in {TurnOutcome.CLARIFY, TurnOutcome.CLARIFY.value}:
            question = payload.get("clarification_question")
            effective = payload.get("effective_question")
            has_question = isinstance(question, str) and question.strip()
            if not has_question and isinstance(effective, str) and effective.strip():
                payload["clarification_question"] = effective
        return payload

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
            f"Value {value!r} is not an unambiguous ISO calendar date.",
            code="invalid_iso_date",
            field="active_value",
        ) from exc
    if parsed.isoformat() != text:
        raise TurnResolutionError(
            f"Value {value!r} is not an unambiguous ISO calendar date.",
            code="invalid_iso_date",
            field="active_value",
        )
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
        raise TurnResolutionError(
            "reference_time must be timezone-aware UTC.",
            code="naive_reference_time",
            field="reference_time",
        )
    known_messages = _known_messages(payload)
    for index, binding in enumerate(resolution.active_bindings):
        _validate_binding(
            binding,
            payload=payload,
            known_messages=known_messages,
            field=f"active_bindings[{index}]",
        )
    resolution = _drop_unattested_scenario_bindings(resolution, payload)
    _validate_effective_question_parameters(resolution, payload)
    if resolution.temporal_intent.kind is TemporalIntentKind.UNBOUNDED:
        if resolution.temporal_intent.requires_snapshot:
            if resolution.outcome is not TurnOutcome.CLARIFY:
                raise TurnResolutionError(
                    "Unbounded temporal expressions cannot authorize a snapshot; clarify instead.",
                    code="unbounded_snapshot",
                    field="temporal_intent",
                )
        elif resolution.outcome is TurnOutcome.RESOLVED and resolution.temporal_intent.anchor_date:
            raise TurnResolutionError(
                "Unbounded temporal expressions cannot carry an exact snapshot date.",
                code="unbounded_snapshot",
                field="temporal_intent",
            )
    return resolution


def _current_message_keeps_prior_scenario(message: str) -> bool:
    """True when the current turn attests an amount/date or anaphorically keeps one."""
    return bool(_parameter_like_tokens(message) or _ANAPHORA_RE.search(message))


def _drop_unattested_scenario_bindings(
    resolution: TurnResolution,
    payload: TurnResolutionInput,
) -> TurnResolution:
    """Drop history-only amounts when the current turn does not keep that scenario.

    Provenance is unchanged: values attested in the current message stay.
    Correction and topic_change replace the operand. A complete question with
    no amount and no anaphora cannot carry a prior operand into retrieval.
    """
    if resolution.outcome is TurnOutcome.CLARIFY:
        return resolution
    drop_kinds = {BindingKind.SCENARIO_PARAMETER, BindingKind.PERIOD_DATE}
    current = payload.current_message
    if resolution.relation in {TurnRelation.CORRECTION, TurnRelation.TOPIC_CHANGE}:
        kept = [
            binding
            for binding in resolution.active_bindings
            if binding.kind not in drop_kinds
            or _literal_value_present(binding.active_value, current)
        ]
    elif not _current_message_keeps_prior_scenario(current):
        kept = [binding for binding in resolution.active_bindings if binding.kind not in drop_kinds]
    else:
        return resolution
    if kept == list(resolution.active_bindings):
        return resolution
    return resolution.model_copy(update={"active_bindings": kept})


def resolve_effective_as_of(
    *,
    request_as_of: datetime | None,
    temporal_intent: TemporalIntent,
    bindings: Sequence[ReferenceBinding],
    reference_time: datetime,
) -> EffectiveSnapshot:
    """Convert validated temporal intent into a UTC snapshot without replacing request as_of."""
    if reference_time.tzinfo is None:
        raise TurnResolutionError(
            "reference_time must be timezone-aware UTC.",
            code="naive_reference_time",
            field="reference_time",
        )
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
    field: str,
) -> None:
    roles = []
    value_references: list[BindingReference] = []
    for reference in binding.references:
        if reference.message_id not in known_messages:
            raise TurnResolutionError(
                f"Binding reference {reference.message_id} is not in the supplied history.",
                code="unknown_message_id",
                field=f"{field}.references",
            )
        history_item = known_messages[reference.message_id]
        expected_role = "user" if history_item is None else history_item.role
        if reference.role != expected_role:
            raise TurnResolutionError(
                f"Binding reference {reference.message_id} has role {reference.role}, "
                f"expected {expected_role}.",
                code="role_mismatch",
                field=f"{field}.references",
            )
        roles.append(reference.role)
        if reference.field == "citation" and not reference.citation_field:
            raise TurnResolutionError(
                "Citation references require a citation_field.",
                code="missing_citation_field",
                field=f"{field}.references",
            )
        if (
            reference.excerpt
            and history_item is not None
            and reference.field == "content"
            and reference.excerpt not in history_item.content
        ):
            raise TurnResolutionError(
                "Binding excerpt is not present in the referenced message.",
                code="excerpt_not_in_message",
                field=f"{field}.references",
            )
        if (
            reference.excerpt
            and history_item is None
            and reference.field == "content"
            and reference.excerpt not in payload.current_message
        ):
            raise TurnResolutionError(
                "Binding excerpt is not present in the current message.",
                code="excerpt_not_in_message",
                field=f"{field}.references",
            )
        if reference.field == "citation":
            allowed = set(CitationIdentity.model_fields) - {"message_id"}
            if reference.role != "assistant" or reference.citation_field not in allowed:
                raise TurnResolutionError(
                    "Invalid citation identity/date field.",
                    code="invalid_citation_field",
                    field=f"{field}.references",
                )
            values = [
                str(getattr(item, reference.citation_field))
                for item in payload.citation_metadata
                if item.message_id == reference.message_id
                and getattr(item, reference.citation_field) is not None
            ]
            if reference.excerpt is not None:
                values = [value for value in values if reference.excerpt == value]
            if not values:
                raise TurnResolutionError(
                    "Citation field is not in the supplied metadata.",
                    code="citation_field_not_in_metadata",
                    field=f"{field}.references",
                )
        else:
            content = payload.current_message if history_item is None else history_item.content
            values = [reference.excerpt or content]
        if any(_literal_value_present(binding.active_value, value) for value in values):
            value_references.append(reference)
    unique_roles = set(roles)
    if binding.origin is BindingOrigin.USER_LITERAL:
        if unique_roles - {"user"}:
            raise TurnResolutionError(
                "user_literal bindings may only reference user messages.",
                code="origin_role_mismatch",
                field=f"{field}.origin",
            )
    elif binding.origin is BindingOrigin.ASSISTANT_REFERENCE:
        if unique_roles - {"assistant"}:
            raise TurnResolutionError(
                "assistant_reference bindings may only identify assistant messages.",
                code="origin_role_mismatch",
                field=f"{field}.origin",
            )
    elif binding.origin is BindingOrigin.USER_ADOPTED_ASSISTANT:
        if "assistant" not in unique_roles or "user" not in unique_roles:
            raise TurnResolutionError(
                "user_adopted_assistant bindings require both the assistant value "
                "and the user adoption instruction.",
                code="adoption_missing_instruction",
                field=f"{field}.origin",
            )
    elif binding.origin is BindingOrigin.CITATION_REFERENCE and any(
        reference.field != "citation" for reference in binding.references
    ):
        raise TurnResolutionError(
            "citation_reference bindings may only cite citation fields.",
            code="origin_role_mismatch",
            field=f"{field}.origin",
        )

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
                "Adoption requires a referenced value and later user instruction.",
                code="adoption_missing_instruction",
                field=f"{field}.references",
            )
    elif not value_references:
        raise TurnResolutionError(
            "Active value is not present in its referenced text or metadata.",
            code="value_not_in_reference",
            field=f"{field}.active_value",
        )


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
                    "Snapshot date is outside the supported calendar.",
                    code="invalid_iso_date",
                    field="temporal_intent.anchor_date",
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
        raise TurnResolutionError(
            "Resolver output is not valid JSON.",
            code="malformed_json",
        ) from exc
    if not isinstance(payload, dict):
        raise TurnResolutionError(
            "Resolver output must be a JSON object.",
            code="malformed_json",
        )
    try:
        return TurnResolution.model_validate(payload)
    except ValidationError as exc:
        loc = exc.errors()[0].get("loc") if exc.errors() else ()
        field = ".".join(str(part) for part in loc) if loc else None
        raise TurnResolutionError(
            "Resolver output does not match the resolution schema.",
            code="schema_mismatch",
            field=field,
        ) from exc


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
