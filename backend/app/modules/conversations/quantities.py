"""Source-preserving numeric normalization shared by claims and evidence.

Classification is deliberately conservative: a period is recognized from syntax,
never merely because its value looks like a year. Semantic verification still
establishes the rule and subject to which a quantity applies.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import regex

QuantityKind = Literal["money", "rate", "period", "locator", "count", "duration", "number"]
_SCALES = {
    "hundred": 100,
    "thousand": 1000,
    "million": 1000000,
    "billion": 1000000000,
    "lakh": 100000,
    "lac": 100000,
    "crore": 10000000,
    "শত": 100,
    "হাজার": 1000,
    "লাখ": 100000,
    "লক্ষ": 100000,
    "কোটি": 10000000,
}
# Keep grouping and token boundaries identical for extraction and arithmetic.
# Western 1,234.56, Indian 1,23,456.78, and plain 1234.56 are valid;
# a partial suffix of a grouped number is never a separate operand.
_NUMBER_BODY = (
    r"(?:\d{1,3}(?:[,٬]\d{2})+[,٬]\d{3}|\d{1,3}(?:[,٬]\d{3})+|\d+)"
    r"(?:\.\d+)?"
)
NUMBER_TOKEN = rf"(?<![\p{{L}}\p{{N}},٬.]){_NUMBER_BODY}(?![\p{{N}},٬]|\.\d)"
_NUMBER = regex.compile(
    rf"(?P<number>{NUMBER_TOKEN})"
    r"(?:\s*(?P<scale>" + "|".join(_SCALES) + r")(?![\p{L}\p{M}]))?",
    regex.I,
)
_CURRENCY = r"(?:\b(?:BDT|Tk|taka|USD|EUR|GBP)\b|[৳$€£]|টাকা(?:র)?)"
_MONEY_CUE = regex.compile(
    (
        "\\b(?:amount|fee|fine|penalty|payable|rebate|t"
        "ax|income|investment|salary|threshold|limit|c"
        "ap|price|cost|value|currency)\\b|টাকা|আয়|আয়|ক"
        "র|রেয়াত|রেয়াত|সীমা|মূল্য|জরিমানা"
    ),
    regex.I,
)


@dataclass(frozen=True, slots=True)
class Quantity:
    kind: QuantityKind
    value: Decimal
    raw: str
    start: int
    end: int
    role: str | None = None
    explicit_currency: bool = False


def normalize_quantities(text: str) -> tuple[Quantity, ...]:
    """Return exact original spans, decimal values and locally established roles."""
    # Digit folding and Markdown masking preserve character offsets.
    folded = "".join(str(int(c)) if c.isdecimal() else c for c in text)
    folded = folded.replace("*", " ").replace("_", " ").replace("`", " ")
    # Recognize the complete period before classifying either endpoint. Currency
    # in the next table cell/line must never turn the short endpoint into money.
    period_spans = [
        (m.start(), m.end())
        for m in regex.finditer(
            ("(?<!\\d)(?:18|19|20|21)\\d{2}\\s*[-\\u2013\\u2014/]\\s*\\d{2,4}(?!\\d)"), folded
        )
    ]
    quantities = []
    for match in _NUMBER.finditer(folded):
        line_start = (
            max(folded.rfind("\n", 0, match.start()), folded.rfind("|", 0, match.start())) + 1
        )
        endings = [p for token in ("\n", "|") if (p := folded.find(token, match.end())) >= 0]
        line_end = min(endings, default=len(folded))
        before = folded[max(line_start, match.start() - 65) : match.start()]
        after = folded[match.end() : min(line_end, match.end() + 45)]
        value = Decimal(match["number"].replace(",", "").replace("٬", ""))
        value *= _SCALES.get((match["scale"] or "").casefold(), 1)
        explicit = bool(
            regex.search(_CURRENCY + r"\s*$", before, regex.I)
            or regex.match(r"\s*" + _CURRENCY, after, regex.I)
        )
        kind: QuantityKind = "number"
        role = None
        in_period = any(
            start <= match.start() and match.end() <= end for start, end in period_spans
        )
        if in_period:
            kind = "period"
            explicit = False
        elif explicit:
            kind = "money"
        elif regex.search(
            ("(?:sections?|articles?|chapters?|s\\.|ধারা|অনুচ্ছেদ)\\s*$"), before, regex.I
        ):
            kind = "locator"
        elif regex.match(r"\s*(?:%|percent\b|শতাংশ)", after, regex.I):
            kind = "rate"
        elif regex.match(r"\s*(?:days?|months?|years?|hours?|দিন|মাস|বছর)\b", after, regex.I):
            kind = "duration"
        elif regex.match(
            r"\s*(?:people|persons?|employees?|workers?|taxpayers?|জন)\b", after, regex.I
        ):
            kind = "count"
        elif (
            regex.search(
                (
                    "(?:\\b(?:AY|FY|year|period|Act|Ordinance|Rules|Regulatio"
                    "ns|Code)|করবর্ষ|অর্থবছর)\\s*[,.:]?\\s*$"
                ),
                before,
                regex.I,
            )
            or (
                1900 <= value <= 2200
                and regex.search(r"\b(?:in|for|during|from|through|until)\s*$", before, regex.I)
            )
            or (1900 <= value <= 2200 and regex.match(r"\s*[-\u2013\u2014/]\s*\d{2,4}\b", after))
            or regex.search(r"\b(?:18|19|20|21)\d{2}\s*[-\u2013\u2014/]\s*$", before)
        ):
            kind = "period"
        elif _MONEY_CUE.search(before + after):
            kind = "money"
        if kind == "money":
            cues = list(_MONEY_CUE.finditer(before))
            # A currency marker is a unit, not the subject of the amount.
            role = next(
                (
                    cue.group().casefold()
                    for cue in reversed(cues)
                    if cue.group().casefold() not in {"টাকা", "currency"}
                ),
                None,
            )
        quantities.append(
            Quantity(
                kind,
                value,
                text[match.start() : match.end()],
                match.start(),
                match.end(),
                role,
                explicit,
            )
        )
    return tuple(quantities)


def money_values(text: str, *, allow_untyped: bool = False) -> set[Decimal]:
    """Numeric monetary values; callers may retain untyped scenario operands."""
    return {
        item.value
        for item in normalize_quantities(text)
        if (item.kind == "money" and (item.explicit_currency or item.value >= 100))
        or (allow_untyped and item.kind == "number" and item.value >= 100)
    }
