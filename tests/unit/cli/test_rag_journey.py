"""Focused tests for the small local RAG journey contract."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.cli.rag_journey import (
    EvidenceAnchor,
    JourneyCase,
    JourneyError,
    RuntimeChunk,
    _comparison_summary,
    _preflight_default_organization,
    aggregate_results,
    build_project_config,
    evaluate_case_result,
    load_manifest,
    normalize_text,
    parse_config_assignment,
    resolve_evidence_anchors,
    sanitize_diagnostics,
    tag_aggregates,
)
from app.cli.rag_journey_cli import _options, _parser
from app.core.config import ResponseMode
from app.modules.conversations.schemas.message import SourceProvenance
from app.platform.jobs.contracts import JobDefinition
from app.platform.jobs.implementations.inline_queue import InlineJobQueue
from app.platform.jobs.names import DOCUMENT_PURGE

pytestmark = pytest.mark.unit


class _OrganizationSession:
    def __init__(self, organization: object | None) -> None:
        self.organization = organization

    async def get(self, _model: object, _identifier: object) -> object | None:
        return self.organization


async def test_default_organization_preflight_fails_without_exact_active_org() -> None:
    with pytest.raises(JourneyError, match="default/local Organization"):
        await _preflight_default_organization(_OrganizationSession(None))

    with pytest.raises(JourneyError, match="default/local Organization"):
        await _preflight_default_organization(
            _OrganizationSession(SimpleNamespace(deleted_at=None, is_active=False))
        )


async def test_default_organization_preflight_accepts_active_default_org() -> None:
    await _preflight_default_organization(
        _OrganizationSession(SimpleNamespace(deleted_at=None, is_active=True))
    )


def test_tax_v1_manifest_has_fixed_ten_cases() -> None:
    manifest = load_manifest()

    assert manifest.key == "tax_v1"
    assert len(manifest.cases) == 10
    assert {"multilingual", "authority", "scope", "refusal"}.issubset(
        {tag for case in manifest.cases for tag in case.tags}
    )


def test_anchor_resolution_uses_source_section_and_phrases() -> None:
    chunk_id = uuid.uuid4()
    document_id = uuid.uuid4()
    anchor = EvidenceAnchor(
        key="rate",
        source="act",
        section="Section 21 — Rate",
        phrases=["15% of eligible investment"],
    )
    chunks = [
        RuntimeChunk(
            id=chunk_id,
            document_id=document_id,
            source_key="act",
            chunk_index=4,
            content="Section 21 — Rate\nA rebate is 15% of eligible investment.",
            metadata={"section_title": "Section 21 — Rate"},
        )
    ]

    assert resolve_evidence_anchors([anchor], chunks) == {"rate": [chunk_id]}


def test_anchor_resolution_fails_on_ambiguity() -> None:
    anchor = EvidenceAnchor(
        key="rate",
        source="act",
        section="Section 21",
        phrases=["15%"],
    )
    chunks = [
        RuntimeChunk(
            id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            source_key="act",
            chunk_index=index,
            content="Section 21 says 15%.",
            metadata={"section_title": "Section 21"},
        )
        for index in range(2)
    ]

    with pytest.raises(JourneyError, match=r"expected 1 chunk.*found 2"):
        resolve_evidence_anchors([anchor], chunks)


def test_numeric_normalization_handles_bangla_digits_and_grouping() -> None:
    assert normalize_text("BDT ৬০,০০০ → BDT ৬,০০০") == "bdt 60000 bdt 6000"  # noqa: RUF001


@pytest.mark.parametrize(
    "raw",
    [
        "embedding.model=text-embedding-3-small",
        "chunking.max_tokens=100",
        "retrieval.fts_regconfig=simple",
        "database.password=secret",
        "unknown.enabled=true",
    ],
)
def test_config_assignments_reject_index_credentials_and_unknown_keys(raw: str) -> None:
    with pytest.raises(JourneyError, match="Unsafe or unknown"):
        parse_config_assignment(raw)


def test_config_assignments_accept_only_valid_project_query_leaves() -> None:
    key, value = parse_config_assignment("retrieval.query_translation_enabled=false")

    assert (key, value) == ("retrieval.query_translation_enabled", False)
    config = build_project_config({key: value, "chat.response_mode": "indexed_then_web"})
    assert config.retrieval.query_translation_enabled is False
    assert config.chat.response_mode is ResponseMode.INDEXED_THEN_WEB


def test_cli_rejects_more_than_one_comparison_variant() -> None:
    args = _parser().parse_args(
        [
            "--compare",
            "retrieval.query_translation_enabled=false",
            "--compare",
            "retrieval.query_translation_enabled=true",
        ]
    )

    with pytest.raises(JourneyError, match="only one --compare"):
        _options(args, configured_job_backend="taskiq")


def _message(*, content: str, metadata: dict[str, object], grounded: bool = True) -> object:
    return SimpleNamespace(
        content=content,
        metadata=metadata,
        citations=[],
        claims=[],
        grounded=grounded,
        insufficient_evidence_reason=None,
        source_provenance=SourceProvenance.NONE,
        retrieval_latency_ms=2,
        provider_latency_ms=3,
        total_latency_ms=5,
    )


def test_no_answer_records_indexed_failure_before_web_eligibility() -> None:
    case = JourneyCase(
        key="unknown",
        tags=["refusal", "fallback"],
        query="Unknown?",
        anchors=[],
        mode="no_answer",
    )
    message = _message(
        content="A later web-backed response is allowed.",
        grounded=False,
        metadata={
            "retrieval_trace": {
                "context_selected": [
                    {
                        "rank": 1,
                        "source_kind": "web",
                        "web_url": "https://example.test/moon",
                    }
                ]
            },
            "evidence_gate": {"sufficient": False, "generation_ran": True},
            "web_search": {"status": "evidence_accepted", "fallback_used": True},
        },
    )

    result = evaluate_case_result(
        case=case,
        message=message,
        anchor_mapping={},
        document_ids={},
        response_mode=ResponseMode.INDEXED_THEN_WEB,
        modifies_expansion_enabled=True,
    )

    assert result["passed"] is True
    assert result["evidence_gate"]["sufficient"] is False
    assert result["fallback"]["fallback_used"] is True


def test_scope_failure_is_localized_to_authority() -> None:
    scoped_id = uuid.uuid4()
    outside_id = uuid.uuid4()
    case = JourneyCase(
        key="scope",
        tags=["scope", "authority"],
        query="Current rate?",
        anchors=[],
        document_scope="old",
        mode="scope_isolation",
    )
    message = _message(
        content="",
        grounded=False,
        metadata={
            "retrieval_trace": {
                "candidates": [{"chunk_id": str(uuid.uuid4()), "document_id": str(outside_id)}],
                "retrieval_selected": [],
                "context_selected": [],
            },
            "current_authority": {"status": "expanded"},
            "evidence_gate": {"sufficient": False},
            "web_search": {"status": "suppressed_scoped_request", "fallback_used": False},
        },
    )

    result = evaluate_case_result(
        case=case,
        message=message,
        anchor_mapping={},
        document_ids={"old": scoped_id},
        response_mode=ResponseMode.INDEXED_THEN_WEB,
        modifies_expansion_enabled=True,
    )

    assert {failure["stage"] for failure in result["failures"]} == {"authority"}


def test_tag_aggregation_and_sanitization_are_maintainable() -> None:
    cases = [
        {
            "passed": True,
            "mode": "answerable",
            "tags": ["multilingual"],
            "timings_ms": {"total": 10},
            "retrieval": {"recall": 1.0},
            "failures": [],
        },
        {
            "passed": False,
            "mode": "no_answer",
            "tags": ["refusal"],
            "timings_ms": {"total": 20},
            "retrieval": {"recall": 1.0},
            "failures": [{"stage": "fallback", "message": "bad"}],
        },
    ]

    assert aggregate_results(cases)["pass_rate"] == 0.5
    assert tag_aggregates(cases)["multilingual"]["pass_rate"] == 1.0
    assert tag_aggregates(cases)["refusal"]["failure_counts"]["fallback"] == 1
    assert sanitize_diagnostics(
        {"api_key": "top-secret", "max_tokens": 100, "nested": {"password": "pw"}}
    ) == {"api_key": "[redacted]", "max_tokens": 100, "nested": {"password": "[redacted]"}}


def test_comparison_highlights_only_affected_tag_subsets() -> None:
    base = {
        "aggregate": {"pass_rate": 0.5, "mean_recall": 0.5, "latency_p50_ms": 10},
        "tag_aggregates": {
            tag: {
                "case_count": 2,
                "pass_rate": 0.5,
                "mean_recall": 0.5,
                "latency_p50_ms": 10,
            }
            for tag in ("multilingual", "authority", "scope", "refusal")
        },
    }
    comparison = {
        "aggregate": {"pass_rate": 0.6, "mean_recall": 0.7, "latency_p50_ms": 15},
        "tag_aggregates": {
            tag: {
                "case_count": 2,
                "pass_rate": 0.6,
                "mean_recall": 0.7,
                "latency_p50_ms": 15,
            }
            for tag in ("multilingual", "authority", "scope", "refusal")
        },
    }

    report = _comparison_summary(
        base,
        comparison,
        key="retrieval.query_translation_enabled",
    )

    assert report["affected_tags"] == ["multilingual"]
    assert report["tags"]["multilingual"]["affected"] is True
    assert report["tags"]["authority"]["affected"] is False


async def test_inline_queue_dispatches_document_purge(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, object]] = []

    async def fake_purge(*, project_id: object, job_id: object) -> None:
        calls.append((project_id, job_id))

    monkeypatch.setattr(
        "app.worker.handlers.document_lifecycle.run_document_purge",
        fake_purge,
    )
    project_id = uuid.uuid4()
    job_id = uuid.uuid4()

    await InlineJobQueue().enqueue(
        JobDefinition(
            name=DOCUMENT_PURGE,
            project_id=project_id,
            document_id=uuid.uuid4(),
            payload={"job_id": str(job_id)},
        )
    )

    assert calls == [(project_id, str(job_id))]
