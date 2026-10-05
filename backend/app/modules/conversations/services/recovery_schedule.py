"""Finite request-local recovery actions; evidence validation stays in repair."""

from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any


@dataclass
class RecoverySchedule:
    deadline: float
    enforce_reserves: bool = True
    clock: Callable[[], float] = field(default_factory=lambda: monotonic)
    counts: dict[str, int] = field(default_factory=dict)
    actions: list[dict[str, Any]] = field(default_factory=list)
    fingerprints: set[tuple[str, str]] = field(default_factory=set)
    stop_reason: str | None = None
    action_limits: dict[str, int] = field(
        default_factory=lambda: {
            "search": 3,
            "structure": 2,
            "delta_review": 1,
            "selector_correction": 1,
        }
    )

    @property
    def search_deadline(self) -> float:
        """Discovery cannot spend the last two seconds required for delta validation."""
        return self.deadline - (2.0 if self.enforce_reserves else 0.0)

    def admit(
        self, kind: str, *, requirement_ids: list[str], fingerprint: str, expected_change: str
    ) -> bool:
        limits = self.action_limits
        remaining = max(0.0, self.deadline - self.clock())
        # Every retrieval action needs a subsequent validated coverage review.
        required_seconds = {
            "search": 6.0,
            "structure": 6.0,
            "delta_review": 2.0,
            "selector_correction": 2.0,
        }[kind]
        terminal = "admitted"
        if remaining <= 0 or (self.enforce_reserves and remaining < required_seconds):
            terminal = "exhausted_budget"
        elif self.counts.get(kind, 0) >= limits[kind]:
            terminal = "action_limit_reached"
        elif (kind, fingerprint) in self.fingerprints:
            terminal = "unchanged_scope_and_evidence"
        self.actions.append(
            {
                "kind": kind,
                "requirement_ids": requirement_ids,
                "expected_change": expected_change,
                "remaining_budget_seconds": round(remaining, 3),
                "validation_reserve_seconds": required_seconds,
                "fingerprint": fingerprint,
                "terminal_result": terminal,
            }
        )
        if terminal != "admitted":
            self.stop_reason = terminal
            return False
        self.counts[kind] = self.counts.get(kind, 0) + 1
        self.fingerprints.add((kind, fingerprint))
        return True

    def complete(self, kind: str, *, changed: bool = True, failed: bool = False) -> None:
        for action in reversed(self.actions):
            if action["kind"] == kind and action["terminal_result"] == "admitted":
                action["terminal_result"] = (
                    "failed" if failed else "completed" if changed else "no_progress"
                )
                return

    def close(self, *, cancelled: bool = False) -> None:
        for action in self.actions:
            if action["terminal_result"] == "admitted":
                action["terminal_result"] = "cancelled" if cancelled else "failed"
