"""Observed-report safety and compatibility without a provider or live corpus."""

from __future__ import annotations

import uuid
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.cli.acceptance_report import normalize_package
from app.core.exceptions import BadRequestError
from app.modules.conversations.schemas.message import AnswerClaim, CitationSnapshot, ClaimEvidence
from app.modules.evaluation.metrics import compute_message_acceptance_metrics
from app.modules.retrieval.acceptance_questions import REMEDIATION_ALIASES, REMEDIATION_QUESTIONS
from app.modules.retrieval.build_acceptance import (
    AcceptanceSetDefinition,
    digest,
    validate_acceptance,
)
from app.modules.retrieval.schemas.acceptance_report import CapturedTurn, ReviewedCaseLabel
from app.modules.retrieval.services.observed_acceptance_service import (
    parity_capture_core,
    public_capture_core,
    require_completed_message,
)
from app.platform.jobs.errors import PermanentJobError
from tests.unit.modules.retrieval.test_phase3_build_acceptance import fixture

pytestmark = pytest.mark.unit


def test_all_sixteen_frozen_questions_preserve_mandatory_and_additional_ids():
    from app.modules.retrieval.build_acceptance import REMEDIATION_CASE_IDS

    assert len(REMEDIATION_QUESTIONS) == len(REMEDIATION_ALIASES) == 16
    assert set(REMEDIATION_QUESTIONS) > REMEDIATION_CASE_IDS
    assert set(REMEDIATION_QUESTIONS) - REMEDIATION_CASE_IDS == {
        "yearless_threshold",
        "historical_threshold",
        "salary_retest",
        "unsupported_retest",
    }
    assert "strictly as a budget proposal" in REMEDIATION_QUESTIONS["Q7"]


def production_fixture():
    build, identity, old = fixture()
    baseline = uuid.uuid4()
    definition = AcceptanceSetDefinition(
        project_id=build.project_id,
        build_id=build.id,
        revision="observed.test.v1",
        certification="production",
        repetitions=3,
        case_expectations={"fixture": "insufficient_evidence"},
    )
    cases = [old.cases[0].model_copy(update={"repetition": n}) for n in range(1, 4)]
    identity = {
        **identity,
        "active_build_id": baseline,
        "conversation_configuration_hash": "7" * 64,
        "acceptance_definition": definition,
    }
    report = {
        "identity": {
            "project_id": str(build.project_id),
            "code": identity["code"],
            "configuration_hash": identity["configuration_hash"],
            "conversation_configuration_hash": identity["conversation_configuration_hash"],
            "source_generation": 7,
            "active_build_id": str(baseline),
        },
        "structures": {str(build.id): "f" * 64},
        "comparison_hash": "8" * 64,
        "cases": [v.model_dump(mode="json") for v in cases],
    }
    artifact = old.model_copy(
        update={
            "certification": "production",
            "cases": cases,
            "report_hash": digest(report["cases"]),
            "acceptance_set_revision": definition.revision,
            "acceptance_set_hash": digest(definition.model_dump(mode="json")),
            "compared_active_build_id": baseline,
            "comparison_report_hash": report["comparison_hash"],
            "observed_report_id": uuid.uuid4(),
            "observed_report_hash": digest(report),
        }
    )
    return build, identity, artifact, report


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "raw_digest",
        "runtime",
        "scope",
        "source_generation",
        "configuration",
        "comparison",
        "cases",
    ],
)
def test_production_receipts_require_bound_observed_reports(mutation):
    build, identity, artifact, report = production_fixture()
    validate_acceptance(artifact, build, **identity, observed_report=report)
    bad = deepcopy(report)
    if mutation == "missing":
        bad = None
    elif mutation == "raw_digest":
        artifact = artifact.model_copy(update={"observed_report_hash": "0" * 64})
    elif mutation == "runtime":
        bad["identity"]["code"] = "0" * 64
    elif mutation == "scope":
        bad["structures"][str(build.id)] = "0" * 64
    elif mutation == "source_generation":
        bad["identity"]["source_generation"] += 1
    elif mutation == "configuration":
        bad["identity"]["conversation_configuration_hash"] = "0" * 64
    elif mutation == "comparison":
        bad["comparison_hash"] = "0" * 64
    else:
        bad["cases"][0]["evidence_hash"] = "0" * 64
    if bad is not None and mutation != "raw_digest":
        artifact = artifact.model_copy(update={"observed_report_hash": digest(bad)})
    with pytest.raises(PermanentJobError) as error:
        validate_acceptance(artifact, build, **identity, observed_report=bad)
    assert error.value.code == "index_acceptance_observed_missing"


@pytest.mark.parametrize("outcome", ["timed_out", "verification_failed", "answered"])
def test_failed_or_wrong_terminal_is_not_corpus_gap(outcome):
    message = SimpleNamespace(
        message_metadata={"terminal_outcome": {"outcome": outcome}}, claims=[], grounded=False
    )
    with pytest.raises(BadRequestError):
        require_completed_message(message, "insufficient_evidence")


def test_stable_published_proof_and_transport_parity_are_required():
    claim = {
        "assertion_id": "A1",
        "claim_id": "1",
        "text": "A documented fact",
        "verification": "supported",
        "grounded": True,
        "evidence": [
            {
                "chunk_id": str(uuid.uuid4()),
                "document_id": str(uuid.uuid4()),
                "chunk_index": 0,
                "citation_index": 1,
                "filename": "source.md",
            }
        ],
    }
    message = SimpleNamespace(
        message_metadata={"terminal_outcome": {"outcome": "answered", "failure_stage": None}},
        claims=[claim],
        grounded=True,
    )
    require_completed_message(message, "answered")
    for key, value in (("verification", "unverified"), ("assertion_id", None), ("evidence", [])):
        invalid = deepcopy(message)
        invalid.claims[0][key] = value
        with pytest.raises(BadRequestError):
            require_completed_message(invalid, "answered")
    raw = {
        "id": str(uuid.uuid4()),
        "content": "A documented fact [1]",
        "grounded": True,
        "claims": [claim],
        "citations": [],
        "metadata": {
            "terminal_outcome": {"outcome": "answered"},
            "execution_runtime_identity": "a" * 64,
            "scope_review_fingerprint": "b" * 64,
        },
    }
    done = {
        **raw,
        "event": "done",
        "assistant_message_id": raw["id"],
        "terminal_outcome": raw["metadata"]["terminal_outcome"],
        "execution_runtime_identity": "a" * 64,
        "scope_review_fingerprint": "b" * 64,
    }
    assert parity_capture_core(done, "sse") == public_capture_core(raw)
    done["content"] = "Different output"
    assert parity_capture_core(done, "sse") != public_capture_core(raw)


@pytest.mark.parametrize(
    "private_key", ["operator_diagnostic", "api_key", "authorization", "reasoning_content"]
)
def test_capture_packages_reject_private_or_credential_fields(private_key):
    with pytest.raises(ValidationError):
        CapturedTurn(
            case_id="Q1",
            repetition=1,
            build_id=uuid.uuid4(),
            user_message_id=uuid.uuid4(),
            assistant_message_id=uuid.uuid4(),
            raw_message={"metadata": {private_key: "secret"}},
        )


@pytest.mark.parametrize(
    "raw", [{"claims": [None]}, {"claims": [{"evidence": [None]}]}, {"citations": "bad"}]
)
def test_malformed_public_proof_fails_cleanly(raw):
    with pytest.raises(BadRequestError):
        public_capture_core(raw)


def test_unknown_source_labels_cannot_be_positive_without_source_proof():
    with pytest.raises(ValidationError):
        ReviewedCaseLabel(
            question="Question", expected="answered", reason="Unknown", inventory_hash="a" * 64
        )
    with pytest.raises(ValidationError):
        ReviewedCaseLabel(
            question="Question",
            expected="insufficient_evidence",
            reason="Missing",
            inventory_hash="a" * 64,
        )
    label = ReviewedCaseLabel(
        question="Question",
        expected="insufficient_evidence",
        reason="Reviewed inventory lacks the requested schedule",
        inventory_hash="a" * 64,
        missing_requirements=["requested fee schedule"],
    )
    assert label.spans == []
    with pytest.raises(ValueError):
        normalize_package(b"x" * 8000001)


def test_acceptance_metrics_reuse_correct_failure_and_abstention_denominators():
    base = {
        "kind": "acceptance",
        "expected_no_answer": True,
        "expected_outcome": "insufficient_evidence",
        "latency_ms": 10,
        "claims": [],
        "grounded": False,
        "insufficient_evidence_reason": "no_retrieval_results",
        "complete_turn_latency_ms": 20,
    }
    rows = [
        {
            **base,
            "execution": {"terminal": {"outcome": value}},
            "attempted_assertion_count": 2,
            "rejected_assertion_count": 2,
        }
        for value in ("insufficient_evidence", "timed_out", "verification_failed")
    ]
    metrics = compute_message_acceptance_metrics(rows)
    assert metrics["correct_abstention_rate"] == pytest.approx(1 / 3)
    assert metrics["timeout_count"] == metrics["verification_failure_count"] == 1
    assert metrics["citation_coverage_status"] == "not_applicable"
    assert metrics["attempted_assertion_count"] == metrics["rejected_assertion_count"] == 6
    assert metrics["published_assertion_count"] == 0
    assert "recall_at_k" not in metrics


@pytest.mark.parametrize(
    "expected,reason,stage",
    [
        ("insufficient_evidence", "no_retrieval_results", "retrieval"),
        ("insufficient_evidence", "known_corpus_gap", "coverage"),
        ("unresolved_authority", "unresolved_authority", "coverage"),
    ],
)
def test_genuine_completed_limitation_semantics_are_accepted(expected, reason, stage):
    message = SimpleNamespace(
        message_metadata={
            "terminal_outcome": {
                "outcome": expected,
                "reason_code": reason,
                "failure_stage": stage,
                "retryable": False,
            }
        },
        claims=[],
        grounded=False,
    )
    assert require_completed_message(message, expected)["reason_code"] == reason
    message.message_metadata["terminal_outcome"]["reason_code"] = "coverage_schema_invalid"
    with pytest.raises(BadRequestError):
        require_completed_message(message, expected)


@pytest.mark.parametrize("outcome", ["verification_failed", "timed_out"])
def test_actual_baseline_failures_are_comparison_only(outcome):
    from app.modules.retrieval.services.observed_acceptance_service import observed_terminal

    message = SimpleNamespace(
        message_metadata={
            "terminal_outcome": {
                "outcome": outcome,
                "reason_code": "claim_verification_failed",
                "failure_stage": "claim_verification",
                "retryable": True,
            }
        },
        claims=[],
        grounded=False,
    )
    assert observed_terminal(message, "answered", release=False)["outcome"] == outcome
    with pytest.raises(BadRequestError):
        observed_terminal(message, "answered", release=True)


def test_production_first_build_has_absent_baseline_and_live_identity():
    build, identity, artifact, report = production_fixture()
    build.manifest["embedding_provider"] = "cohere"
    build.manifest["embedding_model"] = "embed-v4.0"
    definition = identity["acceptance_definition"]
    report["comparison_mode"] = "first_build"
    report["baseline_status"] = "absent"
    report["identity"]["active_build_id"] = None
    artifact = artifact.model_copy(
        update={
            "compared_active_build_id": None,
            "embedding_identity": {
                **artifact.embedding_identity,
                "embedding_provider": "cohere",
                "embedding_model": "embed-v4.0",
            },
            "build_manifest_hash": digest(build.manifest),
            "observed_report_hash": digest(report),
        }
    )
    identity = {**identity, "effective_llm": "openai", "active_build_id": None}
    assert definition.require_comparison is True
    validate_acceptance(artifact, build, **identity, observed_report=report)
    with pytest.raises(PermanentJobError):
        validate_acceptance(
            artifact,
            build,
            **{**identity, "active_build_id": uuid.uuid4()},
            observed_report=report,
        )


@pytest.mark.parametrize("drift", [None, "code", "configuration_hash", "source_generation"])
def test_production_rollback_reuses_exact_accepted_target_and_rejects_identity_drift(drift):
    build, identity, artifact, report = production_fixture()
    identity["active_build_id"] = uuid.uuid4()
    if drift:
        identity[drift] = identity[drift] + 1 if drift == "source_generation" else "0" * 64
        with pytest.raises(PermanentJobError):
            validate_acceptance(artifact, build, **identity, observed_report=report, rollback=True)
    else:
        validate_acceptance(artifact, build, **identity, observed_report=report, rollback=True)
        with pytest.raises(PermanentJobError):
            validate_acceptance(artifact, build, **identity, observed_report=report)


@pytest.mark.parametrize("available", [False, True])
def test_missing_final_persistence_measurement_cannot_certify_latency(available):
    from app.modules.retrieval.services.observed_acceptance_service import completed_turn_timing

    metadata = {
        "lifecycle": {
            "answer_persisted": True,
            "persistence_completed": available,
            "complete_turn_measurement_available": available,
            "processing_ms": 46000,
            "deadline": {"policy": "adaptive_v1", "budget_class": "simple"},
        }
    }
    if not available:
        with pytest.raises(BadRequestError):
            completed_turn_timing(metadata, release=False)
    else:
        assert completed_turn_timing(metadata, release=False)[0] == 46000
    with pytest.raises(BadRequestError):
        completed_turn_timing(metadata, release=True)


async def test_heading_only_fixture_cannot_attest_and_factual_proof_is_uuid_independent():
    from tests.integration.build_acceptance_helpers import grounded_fixture_proof

    def chunk(content):
        return SimpleNamespace(
            id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            chunk_index=0,
            content=content,
            chunk_metadata={},
        )

    heading = chunk("# Section 21")
    fact = chunk("The filing deadline is 30 June.")
    with pytest.raises(AssertionError, match="genuinely grounded"):
        await grounded_fixture_proof([heading])
    for sources in ([heading, fact], [fact, heading]):
        result = await grounded_fixture_proof(sources)
        assert result.grounded is True and result.claims
        assert all(c["verification"] == "supported" for c in result.claims)
        assert any("30 June" in c["text"] for c in result.claims)


def public_proof_fixture():
    from app.modules.conversations.schemas.message import CitationSnapshot

    chunk, document, project = (str(uuid.uuid4()) for _ in range(3))
    citation = CitationSnapshot(
        chunk_id=chunk,
        document_id=document,
        project_id=project,
        filename="source.md",
        chunk_index=0,
    ).model_dump(mode="json")
    return {
        "id": str(uuid.uuid4()),
        "content": "A fact [1]",
        "grounded": True,
        "citations": [citation],
        "claims": [
            {
                "claim_id": "1",
                "assertion_id": "A1",
                "text": "A fact",
                "verification": "supported",
                "grounded": True,
                "evidence": [
                    {
                        "citation_index": 1,
                        "chunk_id": chunk,
                        "document_id": document,
                        "filename": "source.md",
                        "chunk_index": 0,
                    }
                ],
            }
        ],
        "metadata": {"terminal_outcome": {"outcome": "answered"}},
    }


def proof_field_mutation(field, value):
    # Independent edits cover every schema field, including defaults omitted on storage.
    if field.endswith("_id") and field not in {"evidence_unit_id", "evidence_query_variant_id"}:
        return str(uuid.uuid4())
    if field.startswith("source_effective_") or field == "source_published_date":
        return "2026-01-01"
    if field.endswith("_at") or field == "evidence_scope_as_of":
        return "2026-01-01T00:00:00Z"
    if field in {"source_kind"}:
        return "web"  # The source identity validator must reject inconsistent source origins.
    if field in {"coverage_partial", "grounded"}:
        return not value
    if field in {"score"}:
        return 0.75
    if field in {
        "chunk_index",
        "page_number",
        "char_start",
        "char_end",
        "evidence_chunk_char_start",
        "evidence_chunk_char_end",
        "processing_version",
        "source_metadata_generation",
        "source_revision_number",
        "citation_index",
    }:
        return (value or 0) + 1
    if isinstance(value, list):
        if field in {"structural_context", "calculation_references", "requirement_ids"}:
            return ["changed"]
        return [{"text": "changed"}]
    if field in {"config_provenance", "evidence_scope_metadata_filter"}:
        return {"changed": "value"}
    if field in {"verification", "evidence_support", "arithmetic_verification"}:
        return "unverified"
    return "changed"


@pytest.mark.parametrize("field", list(CitationSnapshot.model_fields))
def test_every_public_citation_field_is_bound_to_parity(field):
    raw = public_proof_fixture()
    core = public_capture_core(raw)
    changed = deepcopy(raw)
    changed["citations"][0][field] = proof_field_mutation(field, raw["citations"][0].get(field))
    try:
        changed_core = public_capture_core(changed)
    except BadRequestError:
        return
    assert changed_core != core, field


@pytest.mark.parametrize("field", list(ClaimEvidence.model_fields))
def test_every_assertion_evidence_field_is_bound_to_parity(field):
    raw = public_proof_fixture()
    core = public_capture_core(raw)
    changed = deepcopy(raw)
    evidence = changed["claims"][0]["evidence"][0]
    evidence[field] = proof_field_mutation(field, evidence.get(field))
    try:
        changed_core = public_capture_core(changed)
    except BadRequestError:
        return
    assert changed_core != core, field


@pytest.mark.parametrize("field", [v for v in AnswerClaim.model_fields if v != "evidence"])
def test_every_public_assertion_field_is_bound_to_parity(field):
    raw = public_proof_fixture()
    core = public_capture_core(raw)
    changed = deepcopy(raw)
    claim = changed["claims"][0]
    default = core["claims"][0][field]
    claim[field] = proof_field_mutation(field, default)
    assert public_capture_core(changed) != core, field


def test_public_schema_defaults_are_transport_equivalent():
    raw = public_proof_fixture()
    normalized = public_capture_core(raw)
    expanded = deepcopy(raw)
    expanded["citations"] = deepcopy(normalized["citations"])
    expanded["claims"] = deepcopy(normalized["claims"])
    assert public_capture_core(expanded) == normalized
    for target in (
        expanded["citations"][0],
        expanded["claims"][0],
        expanded["claims"][0]["evidence"][0],
    ):
        for key in list(target):
            if target[key] is None or target[key] == [] or target[key] == {}:
                del target[key]
    expanded["citations"][0].pop("source_kind")
    expanded["claims"][0]["evidence"][0].pop("source_kind")
    expanded["claims"][0].pop("authority_status")
    expanded["claims"][0].pop("claim_kind")
    assert public_capture_core(expanded) == normalized
    done = {
        **expanded,
        "event": "done",
        "assistant_message_id": raw["id"],
        "terminal_outcome": raw["metadata"]["terminal_outcome"],
    }
    assert parity_capture_core(done, "sse") == parity_capture_core(expanded, "get")


@pytest.mark.parametrize("target", ["citation", "claim", "evidence"])
@pytest.mark.parametrize("malformed", [False, True])
def test_unknown_and_malformed_public_proof_are_rejected(target, malformed):
    raw = public_proof_fixture()
    value = {
        "citation": raw["citations"][0],
        "claim": raw["claims"][0],
        "evidence": raw["claims"][0]["evidence"][0],
    }[target]
    if not malformed:
        value["unrecognized_proof"] = "must not disappear"
    elif target == "claim":
        value["grounded"] = "true"
    else:
        value["chunk_index"] = "0"
    with pytest.raises(BadRequestError):
        public_capture_core(raw)


async def publish_approved_matrix(monkeypatch, *, missing=False, optional=False, labels_only=False):
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock, Mock

    from app.models.index_build import IndexBuildState
    from app.modules.retrieval.build_acceptance import REMEDIATION_CASE_IDS, REMEDIATION_PROJECT_ID
    from app.modules.retrieval.schemas.acceptance_report import ObservedAcceptanceReportCreate
    from app.modules.retrieval.services import observed_acceptance_service as module

    project, candidate, active = REMEDIATION_PROJECT_ID, uuid.uuid4(), uuid.uuid4()
    build = SimpleNamespace(
        id=candidate,
        project_id=project,
        state=IndexBuildState.VALIDATED,
        validated_at=datetime.now(UTC),
        manifest={},
    )
    baseline = SimpleNamespace(id=active, state=IndexBuildState.ACTIVE)
    definition = AcceptanceSetDefinition(
        project_id=project,
        build_id=candidate,
        revision="message-journey.v1",
        certification="production",
        repetitions=3,
        case_expectations=dict.fromkeys(sorted(REMEDIATION_CASE_IDS), "insufficient_evidence"),
    )
    frozen = definition.model_dump(mode="json")
    definition_row = SimpleNamespace(definition=frozen, definition_hash=digest(frozen))
    monkeypatch.setattr(
        module.IndexAcceptanceRepository, "definition", AsyncMock(return_value=definition_row)
    )
    identity = {
        "source_generation": 7,
        "conversation_configuration_hash": "a" * 64,
        "code": "b" * 64,
        "config_revision_id": None,
    }
    monkeypatch.setattr(module, "current_acceptance_identity", AsyncMock(return_value=identity))
    monkeypatch.setattr(module, "semantic_snapshot_hash", AsyncMock(return_value="c" * 64))
    labels = {
        k: {
            "question": REMEDIATION_QUESTIONS[k],
            "expected": expected,
            "reason": "Reviewed fixture has no requested evidence",
            "inventory_hash": "c" * 64,
            "missing_requirements": ["requested evidence"],
        }
        for k, expected in definition.case_expectations.items()
    }
    if optional or labels_only:
        for k in set(REMEDIATION_QUESTIONS) - REMEDIATION_CASE_IDS:
            labels[k] = {**labels["Q1"], "question": REMEDIATION_QUESTIONS[k]}
    tuples = [
        (b, k, n)
        for b in (active, candidate)
        for k in definition.case_expectations
        for n in range(1, 4)
    ]
    if missing:
        tuples.pop()
    if optional:
        tuples.append((candidate, "salary_retest", 1))
    scalars = [build, baseline]
    turns = []
    for b, k, n in tuples:
        uid, aid, conv, config = (uuid.uuid4() for _ in range(4))
        created = datetime.now(UTC)
        historical = k not in definition.case_expectations
        metadata = {
            "terminal_outcome": {
                "outcome": "timed_out" if historical else "insufficient_evidence",
                "reason_code": "known_corpus_gap",
                "failure_stage": "coverage",
                "retryable": False,
            },
            "execution_runtime_identity": "b" * 64,
            "scope_review_fingerprint": digest([]),
            "execution": {"attempted_assertions": 0, "rejected_assertions": 0},
            "lifecycle": {
                "processing_ms": 90000 if historical else 20,
                "persistence_completed": True,
                "complete_turn_measurement_available": True,
                "deadline": {"policy": "adaptive_v1", "budget_class": "simple"},
            },
        }
        assistant = SimpleNamespace(
            id=aid,
            conversation_id=conv,
            index_build_id=b,
            source_metadata_generation=7,
            config_snapshot_id=config,
            created_at=created,
            message_metadata=metadata,
            content="Insufficient evidence",
            claims=[],
            citations=[],
            grounded=False,
            retrieval_latency_ms=0,
            insufficient_evidence_reason="no_retrieval_results",
        )
        user = SimpleNamespace(
            id=uid,
            conversation_id=conv,
            config_snapshot_id=config,
            created_at=created - timedelta(seconds=1),
            content=labels[k]["question"],
        )
        scalars.extend([assistant, user, uid, SimpleNamespace(configuration_hash="a" * 64)])
        turns.append(
            {
                "case_id": k,
                "repetition": n,
                "build_id": b,
                "user_message_id": uid,
                "assistant_message_id": aid,
                "raw_message": {
                    "id": str(aid),
                    "content": assistant.content,
                    "grounded": False,
                    "claims": [],
                    "citations": [],
                    "metadata": metadata,
                },
            }
        )
    scalars.append(None)  # No existing immutable report.
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=scalars),
        get=AsyncMock(return_value=SimpleNamespace(active_build_id=active)),
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [])),
        add=Mock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    representative = turns[0]
    raw = representative["raw_message"]
    request = ObservedAcceptanceReportCreate(
        compared_active_build_id=active,
        labels=labels,
        turns=turns,
        parity=[
            {
                "assistant_message_id": representative["assistant_message_id"],
                "transport": "get",
                "raw_message": raw,
            },
            {
                "assistant_message_id": representative["assistant_message_id"],
                "transport": "sse",
                "raw_message": {
                    **raw,
                    "event": "done",
                    "assistant_message_id": raw["id"],
                    "terminal_outcome": raw["metadata"]["terminal_outcome"],
                    "execution_runtime_identity": "b" * 64,
                    "scope_review_fingerprint": digest([]),
                },
            },
        ],
    )
    service = module.ObservedAcceptanceService(
        session, project, message_metrics=compute_message_acceptance_metrics
    )
    report = await service.publish(
        candidate, request, actor_id="fixture", audit=SimpleNamespace(record=Mock())
    )
    assert session.commit.await_count == 1
    return report.report


@pytest.mark.parametrize("optional,labels_only", [(False, False), (False, True), (True, False)])
async def test_approved_twelve_by_three_by_two_matrix_publishes_without_expanding_requirements(
    monkeypatch, optional, labels_only
):
    report = await publish_approved_matrix(monkeypatch, optional=optional, labels_only=labels_only)
    assert len(report["turns"]) == (73 if optional else 72)
    assert len(report["cases"]) == 36
    assert report["metrics"][report["release_build_id"]]["timeout_count"] == 0
    if optional:
        assert report["turns"][-1]["disposition"] == "comparison_only"


async def test_missing_approved_repetition_rejects_observed_report(monkeypatch):
    with pytest.raises(BadRequestError, match="Complete repeated"):
        await publish_approved_matrix(monkeypatch, missing=True, optional=True)


@pytest.mark.parametrize(
    "name",
    ["CitationSourceKind", "ClaimVerification", "CitationSnapshot", "ClaimEvidence", "AnswerClaim"],
)
def test_conversation_public_proof_reexports_shared_contract(name):
    from app.modules.conversations.schemas import message
    from app.platform.domain import message_proof

    assert getattr(message, name) is getattr(message_proof, name)
    assert (
        message.MessageResponse.model_fields["citations"].annotation
        == list[message_proof.CitationSnapshot]
    )
    assert (
        message.MessageResponse.model_fields["claims"].annotation == list[message_proof.AnswerClaim]
    )
    assert (
        message_proof.AnswerClaim.model_fields["evidence"].annotation
        == list[message_proof.ClaimEvidence]
    )
