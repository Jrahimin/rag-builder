"""One post-verification terminal contract shared by all transports."""

from __future__ import annotations

from typing import Any, Literal

from app.modules.conversations.schemas.message import TerminalOutcome


def terminal_outcome(
    *,
    reason: str | None,
    supported_claims: int,
    partial: bool,
    missing_inputs: list[str],
    diagnostics: dict[str, Any],
    coverage: str,
    clarification: bool = False,
    non_knowledge: bool = False,
) -> TerminalOutcome:
    repair = diagnostics.get("knowledge_repair") or {}
    draft = diagnostics.get("answer_draft") or {}
    scope = diagnostics.get("normalized_scope") or {}
    request_scope = {
        key: scope[key]
        for key in (
            "requested_periods",
            "category",
            "jurisdiction",
            "source_restriction",
            "exact_as_of",
            "temporal_basis",
        )
        if key in scope
    }
    supported = sorted(
        {
            str(item.get("requirement_id"))
            for item in (repair.get("coverage") or {}).get("checks", [])
            if item.get("requirement_id")
            and item.get("supported")
            and item.get("fulfillment") == "full"
        }
    )
    unresolved = sorted(
        {
            str(item.get("requirement_id"))
            for item in (repair.get("coverage") or {}).get("checks", [])
            if item.get("requirement_id")
            and (not item.get("supported") or item.get("fulfillment") != "full")
        }
    )
    known_ids = {
        str(item["requirement_id"])
        for item in repair.get("requirements", [])
        if isinstance(item, dict) and item.get("requirement_id")
    }
    unresolved = sorted(set(unresolved) | (known_ids - set(supported)))
    common: dict[str, Any] = {
        "requested_scope": request_scope,
        "supported_requirement_ids": supported,
        "unresolved_requirement_ids": unresolved,
        "coverage": coverage
        if coverage in {"complete", "partial", "incomplete", "not_assessed"}
        else "not_assessed",
    }
    validation = repair.get("validation_failure") or {}
    if draft.get("status") == "failed_verification" and not draft.get("timeout_reason"):
        return TerminalOutcome(
            outcome="verification_failed",
            reason_code=draft.get("reason") or "answer_draft_invalid",
            failure_stage="draft_schema",
            retryable=True,
            next_action="retry",
            **common,
        )
    if validation.get("reason") in {"schema_validation", "selector_retry_invalid_response"}:
        return TerminalOutcome(
            outcome="verification_failed",
            reason_code="coverage_schema_invalid",
            failure_stage="coverage",
            retryable=True,
            next_action="retry",
            **common,
        )
    budget_reason = repair.get("stop_reason") or (repair.get("requirement_progress") or {}).get(
        "stop_reason"
    )
    provider_reason = draft.get("timeout_reason") or (repair.get("provider_context") or {}).get(
        "reason"
    )
    timeout = (
        bool(draft.get("timeout_reason"))
        or reason in {"request_deadline_exceeded", "recovery_deadline_exceeded", "provider_timeout"}
        or (
            not supported_claims
            and (
                budget_reason
                in {
                    "recovery_deadline_exceeded",
                    "exhausted_budget",
                    "insufficient_followup_budget",
                }
                or repair.get("failure_reason") in {"deadline_exceeded", "provider_timeout"}
                or provider_reason in {"request_deadline_exceeded", "recovery_deadline_exceeded"}
            )
        )
    )
    if timeout:
        phase = (
            "draft_schema"
            if draft.get("timeout_reason")
            else str(diagnostics.get("failure_stage") or repair.get("phase") or "coverage")
        )
        timeout_stages: dict[
            str,
            Literal["retrieval", "coverage", "draft_schema", "claim_verification", "persistence"],
        ] = {
            "planning": "coverage",
            "focused_planning": "coverage",
            "coverage_review": "coverage",
            "input_review": "coverage",
            "admission": "retrieval",
            "retrieval": "retrieval",
            "initial_retrieval": "retrieval",
            "web_search": "retrieval",
            "recovery_planning": "coverage",
            "structured_response_retry": "coverage",
            "selector_retry": "coverage",
            "coverage_and_recovery": "coverage",
            "answer_generation": "draft_schema",
            "answer_shape_correction": "draft_schema",
            "claim_verification": "claim_verification",
            "persistence": "persistence",
            "draft_schema": "draft_schema",
            "coverage": "coverage",
        }
        mapped_stage = timeout_stages.get(phase)
        failure_stage = "coverage" if mapped_stage is None else mapped_stage
        return TerminalOutcome(
            outcome="timed_out",
            reason_code=(
                "request_deadline_exceeded"
                if reason == "request_deadline_exceeded"
                or provider_reason == "request_deadline_exceeded"
                else "recovery_deadline_exceeded"
                if provider_reason == "recovery_deadline_exceeded"
                else "provider_timeout"
                if reason == "provider_timeout"
                or draft.get("timeout_reason") == "provider_timeout"
                or repair.get("failure_reason") == "provider_timeout"
                else "recovery_deadline_exceeded"
            ),
            failure_stage=failure_stage,
            retryable=True,
            next_action="retry",
            **common,
        )
    if reason == "claim_verification_failed":
        return TerminalOutcome(
            outcome="verification_failed",
            reason_code=diagnostics.get("verification_failure") or "claim_verification_failed",
            failure_stage="claim_verification",
            retryable=True,
            next_action="retry",
            **common,
        )
    authority_unresolved = (
        diagnostics.get("modifies_authority_scope_status") == "unresolved"
        or (diagnostics.get("scope_current_authority") or {}).get("status")
        in {
            "unresolved",
            "unresolved_authority",
            "dependency_unresolved",
        }
        or repair.get("status") == "dependency_unresolved"
    )
    if reason == "unresolved_authority" and authority_unresolved:
        return TerminalOutcome(
            outcome="unresolved_authority",
            reason_code="unresolved_authority",
            failure_stage="coverage",
            next_action="review_source",
            **common,
        )
    if clarification or (missing_inputs and not reason):
        return TerminalOutcome(
            outcome="needs_input",
            reason_code="missing_scenario_input",
            next_action="supply_input",
            **common,
        )
    if supported_claims and not reason:
        return TerminalOutcome(
            outcome="partial" if partial else "answered",
            reason_code="verified_partial" if partial else "verified_answer",
            **common,
        )
    if non_knowledge:
        return TerminalOutcome(outcome="answered", reason_code="nonfactual_response", **common)
    return TerminalOutcome(
        outcome="insufficient_evidence",
        reason_code=(
            "source_review_incomplete"
            if reason == "unresolved_authority"
            else reason or "no_verified_factual_answer"
        ),
        failure_stage="retrieval" if not diagnostics.get("knowledge_repair") else "coverage",
        next_action="review_source",
        **common,
    )


def terminal_content(result: TerminalOutcome, language: str) -> str | None:
    if result.outcome == "verification_failed":
        if result.failure_stage == "coverage":
            return (
                "অভ্যন্তরীণ যাচাই সমস্যার কারণে উৎস পর্যালোচনা সম্পন্ন হয়নি। এর অর্থ এই নয় যে চাওয়া তথ্য নেই।"
                if language == "bn"
                else "The source review could not be completed because of an internal validation "
                "problem. This does not show that the requested information is missing."
            )
        return (
            "প্রাসঙ্গিক উৎস পাওয়া গেছে, কিন্তু অভ্যন্তরীণ যাচাই সমস্যার কারণে "
            "যাচাইকৃত উত্তর সম্পন্ন হয়নি। আপনার প্রশ্ন পরিবর্তন করতে হবে না।"
            if language == "bn"
            else "I found relevant source passages, but could not complete a verified answer "
            "because an internal response check failed. You do not need to change your question."
        )
    if result.outcome == "timed_out":
        return (
            "সময়সীমার মধ্যে উত্তর যাচাই সম্পন্ন হয়নি। কোনো চূড়ান্ত পরিমাণ বা তথ্য প্রত্যয়ন করা হয়নি।"
            if language == "bn"
            else "I could not finish verifying the requested answer within the time limit. "
            "No final amount or factual answer has been certified."
        )
    if result.outcome == "unresolved_authority":
        return (
            "প্রাসঙ্গিক উৎস পাওয়া গেছে, কিন্তু অনুরোধের সময়কালের জন্য কোন বিধান প্রযোজ্য তা প্রতিষ্ঠা করা যায়নি।"
            if language == "bn"
            else "I found relevant source material, but could not establish which rule "
            "governs the provision for the period you asked about."
        )
    return None


def terminal_projection(
    result: TerminalOutcome,
    *,
    language: str,
    content: str,
    finish_reason: str | None,
    legacy_reason: str | None,
    notices: list[dict[str, Any]],
) -> tuple[str, str | None, str | None, list[dict[str, Any]]]:
    """Project a single finalized cause to every public and persisted transport."""
    replacement = terminal_content(result, language)
    if replacement is not None:
        content = replacement
    if result.outcome in {"timed_out", "verification_failed", "unresolved_authority"}:
        notices = [
            notice
            for notice in notices
            if notice.get("kind")
            not in {
                "insufficient_evidence",
                "verification_failed",
                "unresolved_authority",
                "request_timeout",
                "recovery_timeout",
                "provider_timeout",
            }
        ]
        if result.outcome == "timed_out":
            legacy_reason = (
                result.reason_code
                if result.reason_code
                in {"request_deadline_exceeded", "recovery_deadline_exceeded", "provider_timeout"}
                else "provider_timeout"
            )
            kind = {
                "request_deadline_exceeded": "request_timeout",
                "recovery_deadline_exceeded": "recovery_timeout",
                "provider_timeout": "provider_timeout",
            }[legacy_reason]
        elif result.outcome == "verification_failed":
            legacy_reason = "claim_verification_failed"
            kind = "verification_failed"
        else:
            legacy_reason = "unresolved_authority"
            kind = "unresolved_authority"
        finish_reason = result.reason_code
        notices.append(
            {
                "kind": kind,
                "language": language,
                "text": content,
                "source": {"failure_stage": result.failure_stage, "reason": result.reason_code},
            }
        )
    elif result.outcome == "insufficient_evidence" and legacy_reason == "unresolved_authority":
        legacy_reason = "context_selection_empty"
    return content, finish_reason, legacy_reason, notices
