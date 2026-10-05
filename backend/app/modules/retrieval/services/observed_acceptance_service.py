"""Bind operator captures and source labels to actual persisted production Messages."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any, NoReturn

from pydantic import ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.models.chunk_keyword_index import ChunkKeywordIndex
from app.models.conversation_config_snapshot import ConversationConfigSnapshot
from app.models.document_chunk import DocumentChunk
from app.models.index_acceptance import IndexAcceptanceReport
from app.models.index_build import IndexBuild, IndexBuildState, ProjectIndexPointer
from app.models.message import Message, MessageRole
from app.modules.retrieval.acceptance_questions import REMEDIATION_ALIASES, REMEDIATION_QUESTIONS
from app.modules.retrieval.build_acceptance import (
    REMEDIATION_PROJECT_ID,
    AcceptanceCase,
    AcceptanceSetDefinition,
    current_acceptance_identity,
    digest,
    semantic_snapshot_hash,
)
from app.modules.retrieval.repositories.index_acceptance_repository import IndexAcceptanceRepository
from app.modules.retrieval.schemas.acceptance_report import ObservedAcceptanceReportCreate
from app.platform.audit.contracts import AuditActorType, AuditEventType, AuditOutcome, AuditRecorder
from app.platform.domain.content_hash import content_hash
from app.platform.domain.message_proof import AnswerClaim, CitationSnapshot, ClaimEvidence


class _CapturedCitation(CitationSnapshot):
    model_config = ConfigDict(extra="forbid", strict=True)


class _CapturedEvidence(ClaimEvidence):
    model_config = ConfigDict(extra="forbid", strict=True)


class _CapturedClaim(AnswerClaim):
    model_config = ConfigDict(extra="forbid", strict=True)


def public_capture_core(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize all schema-defined public proof, preserving substantive transport differences."""
    if (
        not isinstance(raw.get("metadata", {}), dict)
        or not isinstance(raw.get("claims", []), list)
        or not isinstance(raw.get("citations", []), list)
    ):
        raise BadRequestError(
            "Malformed captured Message proof.", code="acceptance_observed_invalid"
        )
    if any(not isinstance(v, dict) for v in [*raw.get("claims", []), *raw.get("citations", [])]):
        raise BadRequestError(
            "Malformed captured Message proof.", code="acceptance_observed_invalid"
        )
    if any(
        not isinstance(c.get("evidence", []), list)
        or any(not isinstance(e, dict) for e in c.get("evidence", []))
        for c in raw.get("claims", [])
    ):
        raise BadRequestError(
            "Malformed captured assertion evidence.", code="acceptance_observed_invalid"
        )
    try:
        # JSON strict validation accepts wire UUID/date strings without coercing malformed
        # booleans, numbers or containers. Unknown proof fields must not silently disappear.
        claims = []
        for claim in raw.get("claims", []):
            evidence = [
                _CapturedEvidence.model_validate_json(json.dumps(e), strict=True).model_dump(
                    mode="json"
                )
                for e in claim.get("evidence", [])
            ]
            claims.append(
                _CapturedClaim.model_validate_json(
                    json.dumps({**claim, "evidence": evidence}), strict=True
                ).model_dump(mode="json")
            )
        citations = [
            _CapturedCitation.model_validate_json(json.dumps(c), strict=True).model_dump(
                mode="json"
            )
            for c in raw.get("citations", [])
        ]
    except (ValidationError, TypeError, ValueError) as exc:
        raise BadRequestError(
            "Malformed captured Message proof.", code="acceptance_observed_invalid"
        ) from exc
    return {
        "id": str(raw.get("id")),
        "content": raw.get("content"),
        "grounded": raw.get("grounded"),
        "terminal_outcome": raw.get("terminal_outcome")
        or (raw.get("metadata") or {}).get("terminal_outcome"),
        "claims": claims,
        "citations": citations,
        "runtime": (raw.get("metadata") or {}).get("execution_runtime_identity"),
        "reviews": (raw.get("metadata") or {}).get("scope_review_fingerprint"),
    }


def parity_capture_core(raw: dict[str, Any], transport: str) -> dict[str, Any]:
    if transport == "sse":
        if raw.get("event") != "done":
            raise BadRequestError(
                "SSE parity requires the captured terminal done event.",
                code="acceptance_observed_invalid",
            )
        raw = {
            **raw,
            "id": raw.get("assistant_message_id"),
            "metadata": {
                "execution_runtime_identity": raw.get("execution_runtime_identity"),
                "scope_review_fingerprint": raw.get("scope_review_fingerprint"),
            },
        }
    return public_capture_core(raw)


def require_completed_message(message: Message, expected: str) -> dict[str, Any]:
    metadata = message.message_metadata or {}
    terminal = metadata.get("terminal_outcome") or {}
    completed_reasons = {
        "insufficient_evidence": {
            "known_corpus_gap",
            "no_retrieval_results",
            "below_relevance_threshold",
            "context_selection_empty",
            "no_verified_factual_answer",
            "source_review_incomplete",
            "no_indexed_evidence",
            "coverage_incomplete",
            "insufficient_evidence",
        },
        "unresolved_authority": {"unresolved_authority"},
    }
    abstention = expected in completed_reasons
    invalid_stage = (
        terminal.get("failure_stage") not in {None, "retrieval", "coverage"}
        if abstention
        else terminal.get("failure_stage") is not None
    )
    if (
        terminal.get("outcome") != expected
        or invalid_stage
        or (
            abstention
            and (
                terminal.get("reason_code") not in completed_reasons[expected]
                or terminal.get("retryable") is True
            )
        )
    ):
        raise BadRequestError(
            "Observed terminal outcome failed the reviewed label.",
            code="acceptance_observed_failed",
        )
    claims = message.claims or []
    if expected in {"answered", "partial"} and (message.grounded is not True or not claims):
        raise BadRequestError(
            "Answerable cases require published verified facts.", code="acceptance_observed_failed"
        )
    if expected in {"insufficient_evidence", "unresolved_authority"} and claims:
        raise BadRequestError(
            "Completed abstention cannot publish facts.", code="acceptance_observed_failed"
        )
    if any(
        c.get("verification") != "supported"
        or c.get("grounded") is not True
        or not c.get("assertion_id")
        or not c.get("evidence")
        for c in claims
    ):
        raise BadRequestError(
            "Published facts lack verified stable proof.", code="acceptance_observed_failed"
        )
    if len({c["assertion_id"] for c in claims}) != len(claims):
        raise BadRequestError(
            "Published assertion identities are ambiguous.", code="acceptance_observed_failed"
        )
    return terminal


def observed_terminal(message: Message, expected: str, *, release: bool) -> dict[str, Any]:
    if release:
        return require_completed_message(message, expected)
    terminal = (message.message_metadata or {}).get("terminal_outcome") or {}
    if terminal.get("outcome") not in {
        "answered",
        "partial",
        "insufficient_evidence",
        "unresolved_authority",
        "verification_failed",
        "timed_out",
        "needs_input",
    }:
        raise BadRequestError(
            "Baseline observation has no recognized terminal outcome.",
            code="acceptance_observed_invalid",
        )
    return terminal


def completed_turn_timing(metadata: dict[str, Any], *, release: bool) -> tuple[int, dict[str, Any]]:
    lifecycle = metadata.get("lifecycle") or {}
    deadline = lifecycle.get("deadline") or {}
    elapsed = lifecycle.get("processing_ms")
    if (
        type(elapsed) is not int
        or elapsed < 0
        or not lifecycle.get("persistence_completed")
        or not lifecycle.get("complete_turn_measurement_available")
        or deadline.get("policy") != "adaptive_v1"
        or deadline.get("budget_class") not in {"simple", "complex"}
    ):
        raise BadRequestError(
            "Completed production timing/policy evidence is missing.",
            code="acceptance_observed_invalid",
        )
    assert isinstance(elapsed, int)
    ceiling = 120000 if deadline["budget_class"] == "complex" else 45000
    if release and elapsed > ceiling:
        raise BadRequestError(
            "Observed completed turn exceeded the selected hard ceiling.",
            code="acceptance_observed_failed",
        )
    return elapsed, deadline


class ObservedAcceptanceService:
    def __init__(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        *,
        message_metrics: Callable[[list[dict[str, Any]]], dict[str, Any]],
    ):
        self.session = session
        self.project_id = project_id
        self.message_metrics = message_metrics

    async def publish(
        self,
        build_id: uuid.UUID,
        request: ObservedAcceptanceReportCreate,
        *,
        actor_id: str,
        audit: AuditRecorder,
    ) -> IndexAcceptanceReport:
        def fail(reason: str) -> NoReturn:
            raise BadRequestError(reason, code="acceptance_observed_invalid")

        candidate = await self.session.scalar(
            select(IndexBuild)
            .where(IndexBuild.project_id == self.project_id, IndexBuild.id == build_id)
            .with_for_update()
        )
        if (
            candidate is None
            or candidate.state != IndexBuildState.VALIDATED
            or candidate.validated_at is None
        ):
            fail("Observed comparison requires a sealed project candidate.")
        assert candidate is not None
        pointer = await self.session.get(ProjectIndexPointer, self.project_id, with_for_update=True)
        current_active_id = pointer.active_build_id if pointer else None
        first_build = request.comparison_mode == "first_build"
        active = None
        if first_build:
            if current_active_id is not None or request.compared_active_build_id is not None:
                fail("First-build certification requires an actually absent active baseline.")
            # A missing pointer after prior activation is not a first-build bootstrap.
            prior = await self.session.scalar(
                select(IndexBuild.id)
                .where(
                    IndexBuild.project_id == self.project_id, IndexBuild.activated_at.is_not(None)
                )
                .limit(1)
            )
            if prior is not None:
                fail("This Project already has an activation history.")
        else:
            if (
                current_active_id is None
                or current_active_id != request.compared_active_build_id
                or current_active_id == build_id
            ):
                fail("Comparison baseline is not the current distinct active build.")
            active = await self.session.scalar(
                select(IndexBuild).where(
                    IndexBuild.project_id == self.project_id, IndexBuild.id == current_active_id
                )
            )
            if active is None or active.state != IndexBuildState.ACTIVE:
                fail("Comparison baseline is unavailable.")
        compared_ids = [build_id] if active is None else [build_id, active.id]
        definition_row = await IndexAcceptanceRepository(self.session, self.project_id).definition(
            build_id
        )
        if definition_row is None or definition_row.definition_hash != digest(
            definition_row.definition
        ):
            fail("An immutable approved acceptance definition is required.")
        assert definition_row is not None
        definition = AcceptanceSetDefinition.model_validate(definition_row.definition)
        if definition.certification != "production":
            fail("Observed reports certify production comparisons only.")
        labels = {REMEDIATION_ALIASES.get(k, k): v for k, v in request.labels.items()}
        if len(labels) != len(request.labels):
            fail("Duplicate label aliases.")
        remediation = self.project_id == REMEDIATION_PROJECT_ID or any(
            str(row.get("document_id")) == "300fdea8-48a7-43aa-96a5-d3a020e2a2ad"
            for row in (candidate.manifest or {}).get("documents", [])
        )
        if remediation and (
            not set(labels).issubset(REMEDIATION_QUESTIONS)
            or any(v.question != REMEDIATION_QUESTIONS[k] for k, v in labels.items())
        ):
            fail("Remediation labels must retain their frozen question identity.")
        if not set(definition.case_expectations).issubset(labels) or any(
            labels[k].expected != v for k, v in definition.case_expectations.items()
        ):
            fail("Source-reviewed labels differ from the approved set.")
        identity = await current_acceptance_identity(self.session, self.project_id)
        structures = {
            str(build_id): await semantic_snapshot_hash(self.session, self.project_id, candidate)
        }
        if active is not None:
            structures[str(active.id)] = await semantic_snapshot_hash(
                self.session, self.project_id, active
            )
        for label in labels.values():
            if label.inventory_hash != structures[str(build_id)]:
                fail("Source review inventory is stale.")
            for span in label.spans:
                chunk = await self.session.scalar(
                    select(DocumentChunk)
                    .join(ChunkKeywordIndex, ChunkKeywordIndex.chunk_id == DocumentChunk.id)
                    .where(
                        DocumentChunk.project_id == self.project_id,
                        ChunkKeywordIndex.project_id == self.project_id,
                        ChunkKeywordIndex.index_build_id == build_id,
                        DocumentChunk.id == span.chunk_id,
                    )
                )
                if (
                    chunk is None
                    or content_hash(chunk.content) != span.chunk_hash
                    or chunk.char_start is None
                    or span.char_start < chunk.char_start
                    or span.char_end > chunk.char_start + len(chunk.content)
                    or chunk.content[
                        span.char_start - chunk.char_start : span.char_end - chunk.char_start
                    ]
                    != span.quote
                ):
                    fail("Reviewed label source span is not exact candidate proof.")
        from app.models.index_scope_review import IndexScopeReview

        review_rows = (
            await self.session.scalars(
                select(IndexScopeReview)
                .where(
                    IndexScopeReview.project_id == self.project_id,
                    IndexScopeReview.build_id.in_(compared_ids),
                )
                .order_by(IndexScopeReview.chunk_id)
            )
        ).all()
        review_hashes = {
            str(b): digest(
                [
                    {"chunk_id": str(v.chunk_id), "review_hash": v.review_hash}
                    for v in review_rows
                    if v.build_id == b
                ]
            )
            for b in compared_ids
        }
        expected_ids = {
            (str(b), k, n)
            for b in compared_ids
            for k in definition.case_expectations
            for n in range(1, definition.repetitions + 1)
        }
        allowed_ids = {
            (str(b), k, n)
            for b in compared_ids
            for k in labels
            for n in range(1, definition.repetitions + 1)
        }
        seen = set()
        message_ids = set()
        cases = []
        normalized = []
        by_message = {}
        configuration_hashes = set()
        metric_rows: dict[str, list[dict[str, Any]]] = {str(b): [] for b in compared_ids}
        for capture in request.turns:
            case_id = REMEDIATION_ALIASES.get(capture.case_id, capture.case_id)
            key = (str(capture.build_id), case_id, capture.repetition)
            if key not in allowed_ids or key in seen or capture.assistant_message_id in message_ids:
                fail("Missing, duplicated or foreign comparison case.")
            seen.add(key)
            message_ids.add(capture.assistant_message_id)
            assistant = await self.session.scalar(
                select(Message).where(
                    Message.project_id == self.project_id,
                    Message.id == capture.assistant_message_id,
                    Message.role == MessageRole.ASSISTANT,
                )
            )
            user = await self.session.scalar(
                select(Message).where(
                    Message.project_id == self.project_id,
                    Message.id == capture.user_message_id,
                    Message.role == MessageRole.USER,
                )
            )
            label = labels[case_id]
            if (
                assistant is None
                or user is None
                or assistant.conversation_id != user.conversation_id
                or user.content != label.question
                or assistant.index_build_id != capture.build_id
                or assistant.source_metadata_generation != identity["source_generation"]
                or assistant.config_snapshot_id != user.config_snapshot_id
                or assistant.created_at < user.created_at
            ):
                fail("Capture is not the labeled production turn.")
            assert assistant is not None and user is not None
            preceding = await self.session.scalar(
                select(Message.id)
                .where(
                    Message.project_id == self.project_id,
                    Message.conversation_id == assistant.conversation_id,
                    Message.role == MessageRole.USER,
                    Message.created_at <= assistant.created_at,
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(1)
            )
            if preceding != user.id:
                fail("Capture's user turn is not the preceding production request.")
            snapshot = await self.session.scalar(
                select(ConversationConfigSnapshot).where(
                    ConversationConfigSnapshot.project_id == self.project_id,
                    ConversationConfigSnapshot.id == assistant.config_snapshot_id,
                )
            )
            if (
                snapshot is None
                or snapshot.configuration_hash != identity["conversation_configuration_hash"]
            ):
                fail("Message configuration differs from accepted runtime settings.")
            assert snapshot is not None
            configuration_hashes.add(snapshot.configuration_hash)
            metadata = assistant.message_metadata or {}
            if (
                metadata.get("execution_runtime_identity") != identity["code"]
                or metadata.get("scope_review_fingerprint") != review_hashes[str(capture.build_id)]
            ):
                fail("Message runtime or reviewed scope changed.")
            release = capture.build_id == build_id and case_id in definition.case_expectations
            terminal = observed_terminal(assistant, label.expected, release=release)
            expected_raw = {
                "id": str(assistant.id),
                "content": assistant.content,
                "grounded": assistant.grounded,
                "claims": assistant.claims,
                "citations": assistant.citations,
                "metadata": metadata,
            }
            core = public_capture_core(expected_raw)
            if digest(public_capture_core(capture.raw_message)) != digest(core):
                fail("Raw transport Message differs from persisted proof.")
            by_message[str(assistant.id)] = core
            for citation in assistant.citations or []:
                if citation.get("source_kind", "knowledge") != "knowledge" or str(
                    citation.get("index_build_id")
                ) != str(capture.build_id):
                    fail("Citation escaped the pinned indexed corpus.")
                chunk = await self.session.scalar(
                    select(DocumentChunk)
                    .join(ChunkKeywordIndex, ChunkKeywordIndex.chunk_id == DocumentChunk.id)
                    .where(
                        DocumentChunk.project_id == self.project_id,
                        ChunkKeywordIndex.project_id == self.project_id,
                        ChunkKeywordIndex.index_build_id == capture.build_id,
                        DocumentChunk.id == uuid.UUID(str(citation.get("chunk_id"))),
                    )
                )
                if chunk is None or citation.get("chunk_hash") != content_hash(chunk.content):
                    fail("Published citation is not attested build proof.")
            cited_ids = {str(c.get("chunk_id")) for c in assistant.citations or []}
            if any(
                str(e.get("chunk_id")) not in cited_ids
                for c in assistant.claims or []
                for e in c.get("evidence", [])
            ):
                fail("Claim evidence has no bound citation.")
            elapsed, deadline = completed_turn_timing(metadata, release=release)
            metric_row = {
                "kind": "acceptance",
                "expected_no_answer": label.expected
                in {"insufficient_evidence", "unresolved_authority"},
                "expected_outcome": label.expected,
                "execution": {"terminal": terminal},
                "grounded": assistant.grounded,
                "claims": assistant.claims,
                "citation_coverage_status": "applicable" if assistant.claims else "not_applicable",
                "complete_turn_latency_ms": elapsed,
                "search_latency_ms": assistant.retrieval_latency_ms or 0,
                "latency_ms": assistant.retrieval_latency_ms or 0,
                "insufficient_evidence_reason": assistant.insufficient_evidence_reason,
                "published_assertion_count": len(assistant.claims or []),
                "budget_class": deadline["budget_class"],
            }
            counts = metadata.get("execution") or {}
            if (
                type(counts.get("attempted_assertions")) is not int
                or type(counts.get("rejected_assertions")) is not int
            ):
                fail("Actual attempted/rejected publication counts are missing.")
            metric_row["attempted_assertion_count"] = counts["attempted_assertions"]
            metric_row["rejected_assertion_count"] = counts["rejected_assertions"]
            if case_id in definition.case_expectations:
                metric_rows[str(capture.build_id)].append(metric_row)
            evidence_hash = digest(core)
            if release:
                cases.append(
                    AcceptanceCase(
                        case_id=case_id,
                        repetition=capture.repetition,
                        expected=label.expected,
                        actual=terminal["outcome"],
                        evidence_hash=evidence_hash,
                        assertions_verified=True,
                        scope_verified=True,
                    ).model_dump(mode="json")
                )
            normalized.append(
                {
                    "case_id": case_id,
                    "repetition": capture.repetition,
                    "build_id": str(capture.build_id),
                    "user_message_id": str(user.id),
                    "assistant_message_id": str(assistant.id),
                    "raw_message": core,
                    "raw_capture_hash": digest(capture.raw_message),
                    "proof_hash": evidence_hash,
                    "policy": deadline,
                    "metrics": metric_row,
                    "disposition": "release" if release else "comparison_only",
                }
            )
        if not expected_ids.issubset(seen) or len(configuration_hashes) != 1:
            fail("Complete repeated active/candidate comparison is required.")
        parity_transports = set()
        for parity in request.parity:
            if str(parity.assistant_message_id) not in by_message or digest(
                parity_capture_core(parity.raw_message, parity.transport)
            ) != digest(by_message[str(parity.assistant_message_id)]):
                fail("SSE/GET parity capture differs from production proof.")
            parity_transports.add(parity.transport)
        if parity_transports != {"sse", "get"}:
            fail("Representative SSE and persisted GET parity are required.")
        metrics = {}
        for b, rows in metric_rows.items():
            metrics[b] = self.message_metrics(rows)
            for budget_class, target in (("simple", 30000), ("complex", 90000)):
                subset = [v for v in rows if v["budget_class"] == budget_class]
                if (
                    b == str(build_id)
                    and subset
                    and float(self.message_metrics(subset)["complete_turn_latency_p95_ms"]) > target
                ):
                    fail("Observed complete-turn p95 misses the selected policy.")
        cases.sort(key=lambda c: (c["case_id"], c["repetition"]))
        report = {
            "version": request.version,
            "comparison_mode": request.comparison_mode,
            "baseline_status": "absent" if first_build else "observed",
            "identity": {
                **identity,
                "project_id": str(self.project_id),
                "config_revision_id": str(identity["config_revision_id"])
                if identity["config_revision_id"]
                else None,
                "active_build_id": str(active.id) if active else None,
            },
            "structures": structures,
            "labels": {
                k: {**v.model_dump(mode="json"), "reviewer": actor_id} for k, v in labels.items()
            },
            "turns": normalized,
            "parity": [
                {
                    "assistant_message_id": str(v.assistant_message_id),
                    "transport": v.transport,
                    "raw_message": parity_capture_core(v.raw_message, v.transport),
                    "raw_capture_hash": digest(v.raw_message),
                }
                for v in request.parity
            ],
            "metrics": metrics,
            "release_build_id": str(build_id),
            "comparison_only_build_id": str(active.id) if active else None,
            "cases": cases,
            "comparison_hash": digest(normalized),
        }
        value_hash = digest(report)
        existing = await self.session.scalar(
            select(IndexAcceptanceReport).where(
                IndexAcceptanceReport.project_id == self.project_id,
                IndexAcceptanceReport.build_id == build_id,
                IndexAcceptanceReport.report_hash == value_hash,
            )
        )
        if existing:
            return existing
        row = IndexAcceptanceReport(
            id=uuid.uuid4(),
            project_id=self.project_id,
            build_id=build_id,
            report_hash=value_hash,
            report=report,
            created_by=actor_id,
        )
        self.session.add(row)
        audit.record(
            event_type=AuditEventType.INDEX_BUILD_ACCEPTED,
            actor_type=AuditActorType.OPERATOR,
            actor_id=actor_id,
            resource_type="index_acceptance_report",
            resource_id=row.id,
            outcome=AuditOutcome.SUCCESS,
            detail={"build_id": str(build_id), "report_hash": value_hash},
        )
        await self.session.commit()
        await self.session.refresh(row)
        return row
