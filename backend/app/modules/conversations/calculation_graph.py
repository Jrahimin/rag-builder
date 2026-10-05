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
