"""Bounded Decimal arithmetic; source applicability is verified separately."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CalculationNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=80)
    operation: Literal[
        "addition",
        "subtraction",
        "multiplication",
        "division",
        "minimum",
        "maximum",
        "ordered_brackets",
    ]
    operands: list[str] = Field(min_length=1, max_length=24)
    result: Decimal

    @model_validator(mode="after")
    def finite_result(self) -> CalculationNode:
        if not self.result.is_finite() or abs(self.result) > Decimal("1e30"):
            raise ValueError("Calculation result must be finite and bounded")
        return self


class CalculationGraph(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    nodes: list[CalculationNode] = Field(default_factory=list, max_length=32)

    def component_expressions(
        self,
        inputs: dict[str, Decimal],
        sources: dict[str, Decimal],
        bindings: dict[str, dict] | None = None,
        *,
        comparison_operand_pairs: set[frozenset[str]] | None = None,
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Keep independent arithmetic; malformed/foreign identities are fatal.

        A local arithmetic or dimensional failure poisons its descendants only.
        """
        values = {
            **{f"input:{k}": v for k, v in inputs.items()},
            **{f"source:{k}": v for k, v in sources.items()},
        }
        available = set(values)
        for node in self.nodes:
            key = f"node:{node.id}"
            if key in available or any(ref not in available for ref in node.operands):
                raise ValueError("Duplicate, foreign or forward calculation reference")
            available.add(key)
        expressions, failures = {}, {}
        for node in self.nodes:
            if any(ref.startswith("node:") and ref[5:] in failures for ref in node.operands):
                failures[node.id] = "dependency_failed"
                continue
            try:
                scoped = []
                derived: dict[str, str | None] = {}
                if bindings is not None:
                    if any(ref not in bindings for ref in node.operands):
                        raise ValueError("unestablished_units")
                    scoped = [bindings[ref] for ref in node.operands]
                    if any(item.get("period_unresolved") for item in scoped):
                        raise ValueError("unestablished_period")
                    for dimension in ("period", "category"):
                        known = {str(item[dimension]) for item in scoped if item.get(dimension)}
                        if len(known) > 1:
                            if (
                                dimension == "period"
                                and node.operation == "subtraction"
                                and len(scoped) == 2
                                and comparison_operand_pairs
                                and any(
                                    frozenset((left, right)) in comparison_operand_pairs
                                    for left in scoped[0].get("comparison_requirement_ids", ())
                                    for right in scoped[1].get("comparison_requirement_ids", ())
                                )
                            ):
                                derived[dimension] = "comparison:" + ",".join(sorted(known))
                                continue
                            raise ValueError(f"incompatible_{dimension}")
                        derived[dimension] = next(iter(known), None)
                    units = [item.get("unit") for item in scoped]
                    if any(unit in {None, "", "money:unspecified"} for unit in units):
                        raise ValueError("unestablished_units")
                    scalar_units = {"scalar", "ratio"}
                    if node.operation in {"addition", "subtraction", "minimum", "maximum"}:
                        if len(set(units)) != 1:
                            raise ValueError("incompatible_units")
                        unit = units[0]
                    elif node.operation == "multiplication":
                        dimensions = [unit for unit in units if unit not in scalar_units]
                        if len(dimensions) > 1:
                            raise ValueError("unestablished_derived_units")
                        unit = (
                            dimensions[0]
                            if dimensions
                            else "ratio"
                            if "ratio" in units
                            else "scalar"
                        )
                    elif node.operation == "division":
                        if len(units) != 2:
                            raise ValueError("division_requires_two_operands")
                        if units[1] in scalar_units:
                            unit = units[0]
                        elif units[0] == units[1]:
                            unit = "ratio"
                        else:
                            raise ValueError("unestablished_derived_units")
                    else:
                        if (
                            len(units) < 3
                            or len(units) % 2 != 1
                            or units[0] not in {"BDT", "USD", "EUR", "GBP"}
                        ):
                            raise ValueError("incompatible_bracket_units")
                        if any(value != units[0] for value in units[1::2]) or any(
                            value != "ratio" for value in units[2::2]
                        ):
                            raise ValueError("incompatible_bracket_units")
                        unit = units[0]
                    derived["unit"] = unit
                # Rebind already verified prior-node values as trusted temporary
                # inputs to the existing strict Decimal verifier.
                quantities = {f"operand{i}": values[ref] for i, ref in enumerate(node.operands)}
                rebound = node.model_copy(
                    update={"operands": [f"input:{key}" for key in quantities]}
                )
                one = CalculationGraph(nodes=[rebound])
                expressions[node.id] = one.expressions(quantities, {})[node.id]
                values[f"node:{node.id}"] = node.result
                if bindings is not None and scoped:
                    bindings[f"node:{node.id}"] = {
                        **derived,
                        "value": str(node.result),
                        "origin": "derived",
                        "role": node.operation,
                    }
            except ValueError as exc:
                failures[node.id] = str(exc)
        return expressions, failures

    def expressions(
        self, inputs: dict[str, Decimal], sources: dict[str, Decimal]
    ) -> dict[str, str]:
        """Render verified operations, operands and outputs without model arithmetic."""
        results = self.verify(inputs, sources)
        values = {
            **{f"input:{k}": v for k, v in inputs.items()},
            **{f"source:{k}": v for k, v in sources.items()},
        }
        expressions: dict[str, str] = {}
        for node in self.nodes:
            operands = [format(values[ref], "f") for ref in node.operands]
            operator = {
                "addition": " + ",
                "subtraction": " - ",
                "multiplication": " * ",
                "division": " / ",
            }.get(node.operation)
            left = (
                operator.join(operands)
                if operator
                else (node.operation + "(" + ", ".join(operands) + ")")
            )
            expressions[node.id] = left + " = " + format(results[node.id], "f")
            values[f"node:{node.id}"] = results[node.id]
        return expressions

    def verify(
        self, inputs: dict[str, Decimal], source_quantities: dict[str, Decimal]
    ) -> dict[str, Decimal]:
        """References are input:key, source:key, or node:key; literals are forbidden.

        Brackets use income followed by width/rate pairs in source order. Rates
        are fractions, widths are finite nonnegative amounts. No implicit cap,
        rounding, threshold, or legal applicability is introduced here.
        """
        values = {
            **{f"input:{k}": v for k, v in inputs.items()},
            **{f"source:{k}": v for k, v in source_quantities.items()},
        }
        results: dict[str, Decimal] = {}
        with localcontext() as ctx:
            ctx.prec = 40
            for node in self.nodes:
                if node.id in results or any(ref not in values for ref in node.operands):
                    raise ValueError("Duplicate node or unverified/forward operand reference")
                operands = [values[ref] for ref in node.operands]
                if any(not v.is_finite() or abs(v) > Decimal("1e30") for v in operands):
                    raise ValueError("Operand must be finite and bounded")
                try:
                    match node.operation:
                        case "addition":
                            result = sum(operands, Decimal(0))
                        case "subtraction" | "division":
                            if len(operands) != 2:
                                raise ValueError("Subtraction/division require two operands")
                            if node.operation == "division" and operands[1] == 0:
                                raise ValueError("Division by zero")
                            result = (
                                operands[0] - operands[1]
                                if node.operation == "subtraction"
                                else operands[0] / operands[1]
                            )
                        case "multiplication":
                            result = Decimal(1)
                            for value in operands:
                                result *= value
                        case "minimum":
                            result = min(operands)
                        case "maximum":
                            result = max(operands)
                        case "ordered_brackets":
                            if len(operands) < 3 or len(operands) % 2 != 1 or operands[0] < 0:
                                raise ValueError(
                                    "Brackets require income and ordered width/rate pairs"
                                )
                            remaining, result = operands[0], Decimal(0)
                            for width, rate in zip(operands[1::2], operands[2::2], strict=True):
                                if width < 0 or not 0 <= rate <= 1:
                                    raise ValueError("Invalid bracket width/rate")
                                taxed = min(remaining, width)
                                result += taxed * rate
                                remaining -= taxed
                            if remaining:
                                raise ValueError(
                                    "Ordered brackets do not cover the supplied income"
                                )
                except InvalidOperation as exc:
                    raise ValueError("Invalid Decimal operation") from exc
                if result != node.result or not result.is_finite() or abs(result) > Decimal("1e30"):
                    raise ValueError(f"Calculation mismatch at {node.id}")
                results[node.id] = result
                values[f"node:{node.id}"] = result
        return results
