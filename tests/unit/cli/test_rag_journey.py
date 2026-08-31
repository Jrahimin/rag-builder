"""Focused tests for the small local RAG journey contract."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.cli.rag_journey import (
    SAFE_CONFIG_KEYS,
    EvidenceAnchor,
    JourneyCase,
    JourneyError,
    JourneySource,
    RuntimeChunk,
    _comparison_summary,
    _ensure_indexed,
    _preflight_default_organization,
    _project_ai_leaf_paths,
    aggregate_results,
    build_project_config,
    evaluate_case_result,
    load_manifest,
    normalize_text,
    parse_config_assignment,
    resolve_evidence_anchors,
    sanitize_diagnostics,
    source_purge_order,
    tag_aggregates,
)
from app.cli.rag_journey_cli import _options, _parser
from app.core.config import ResponseMode
from app.modules.conversations.schemas.message import (
    InsufficientEvidenceReason,
    SourceProvenance,
)
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


def test_tax_v1_fixture_requires_all_eligible_categories_and_stale_correction() -> None:
    manifest = load_manifest()
    cases = {case.key: case for case in manifest.cases}

    assert cases["eligible_investments_scoped"].expected_tokens == [
        "approved savings certificates",
        "approved retirement contributions",
        "approved life-insurance premiums",
    ]
    correction = cases["stale_rebate_correction"].correction
    assert correction is not None
    assert correction.old_tokens == ["15%", "9000"]
    assert correction.new_tokens == ["10%", "6000"]
    assert {
        "incorrect",
        "instead",
        "rather than",
        "no longer",
        "not current",
        "old",
        "previous",
        "changed",
        "superseded",
        "historical",
    }.issubset(correction.markers)
    finance = next(source for source in manifest.sources if source.key == "finance_2026")
    assert finance.modified_provisions["tax_2023"] == [
        "Section 10 — Tax-Free Threshold",
        "Section 21 — Investment Rebate Rate",
        "Section 40 — Individual Example",
    ]


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
        "retrieval.embedding_set_version=3",
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


def test_empty_baseline_inherits_deployment_config_without_implicit_policy_overrides() -> None:
    config = build_project_config({})
    payload = config.model_dump(exclude_none=True)

    assert payload.get("source_policy_mode") is None
    assert payload.get("chat", {}).get("include_citations") is None
    assert payload.get("retrieval", {}).get("modifies_expansion_mode") is None
    assert payload.get("retrieval", {}).get("modifies_expansion_enabled") is None

    explicit = build_project_config({"source_policy_mode": "enforce"})
    assert explicit.source_policy_mode.value == "enforce"
    assert explicit.chat.include_citations is None
    assert explicit.retrieval.modifies_expansion_mode is None


def test_comparison_allowlist_stays_closed_when_project_config_schema_grows() -> None:
    """A future embedding/index leaf must opt in explicitly, never by discovery."""
    assert _project_ai_leaf_paths() >= SAFE_CONFIG_KEYS
    assert "retrieval.embedding_set_version" not in SAFE_CONFIG_KEYS
    with pytest.raises(JourneyError, match="Unsafe or unknown"):
        build_project_config({"retrieval.embedding_set_version": 3})


def test_source_purge_order_deletes_modifiers_before_targets() -> None:
    source_2023 = JourneySource(
        key="tax_2023",
        filename="tax.md",
        title="Tax",
        revision_label="2023",
        source_type="synthetic_statute",
        published_date="2023-07-01",
        effective_from="2023-07-01",
    )
    source_2026 = JourneySource(
        key="finance_2026",
        filename="finance.md",
        title="Finance",
        revision_label="2026",
        source_type="synthetic_statute",
        published_date="2026-07-01",
        effective_from="2026-07-01",
        modifies=["tax_2023"],
    )

    assert source_purge_order([source_2023, source_2026]) == ["finance_2026", "tax_2023"]
    assert source_purge_order([source_2026, source_2023]) == ["finance_2026", "tax_2023"]


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


def _message(
    *,
    content: str,
    metadata: dict[str, object],
    grounded: bool = True,
    insufficient_evidence_reason: InsufficientEvidenceReason | None = None,
) -> object:
    return SimpleNamespace(
        content=content,
        metadata=metadata,
        citations=[],
        claims=[],
        grounded=grounded,
        insufficient_evidence_reason=insufficient_evidence_reason,
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


def test_indexed_only_unknown_requires_refusal_before_generation_or_web() -> None:
    case = JourneyCase(
        key="unknown",
        tags=["refusal", "fallback"],
        query="Unknown?",
        anchors=[],
        mode="no_answer",
    )
    message = _message(
        content="I do not have enough indexed evidence.",
        grounded=False,
        insufficient_evidence_reason=InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD,
        metadata={
            "retrieval_trace": {"context_selected": []},
            "evidence_gate": {"sufficient": False, "generation_ran": False},
            "web_search": {"status": "not_requested", "fallback_used": False},
        },
    )

    result = evaluate_case_result(
        case=case,
        message=message,
        anchor_mapping={},
        document_ids={},
        response_mode=ResponseMode.INDEXED_ONLY,
        modifies_expansion_enabled=True,
    )

    assert result["passed"] is True
    assert result["evidence_gate"]["sufficient"] is False
    assert result["admitted"] == []
    assert result["insufficient_evidence_reason"] is not None
    assert result["evidence_gate"]["generation_ran"] is False
    assert result["fallback"]["status"] == "not_requested"


def test_stale_claim_requires_a_correction_marker_not_just_new_numbers() -> None:
    case = JourneyCase(
        key="stale",
        tags=["authority", "stale_rule"],
        query="Correct the old calculation.",
        anchors=[],
        expected_tokens=["10%", "6000"],
        correction={
            "old_tokens": ["15%", "9000"],
            "new_tokens": ["10%", "6000"],
            "markers": ["instead"],
        },
    )
    message = _message(
        content="15% and 9000; 10% and 6000.",
        metadata={"evidence_gate": {"sufficient": True}, "web_search": {}},
    )

    result = evaluate_case_result(
        case=case,
        message=message,
        anchor_mapping={},
        document_ids={},
        response_mode=ResponseMode.INDEXED_ONLY,
        modifies_expansion_enabled=True,
    )

    assert any("explicitly correct" in item["message"] for item in result["failures"])


def test_stale_correction_need_not_repeat_every_stale_literal() -> None:
    case = JourneyCase(
        key="stale",
        tags=["authority", "stale_rule"],
        query="Correct the old calculation.",
        anchors=[],
        expected_tokens=["10%", "6000"],
        correction={
            "old_tokens": ["15%", "9000"],
            "new_tokens": ["10%", "6000"],
            "markers": ["incorrect"],
        },
    )
    message = _message(
        content="That claim is incorrect. The current result is 10%, or 6000.",
        metadata={
            "evidence_gate": {"sufficient": True},
            "web_search": {"status": "not_requested", "fallback_used": False},
        },
    )

    result = evaluate_case_result(
        case=case,
        message=message,
        anchor_mapping={},
        document_ids={},
        response_mode=ResponseMode.INDEXED_ONLY,
        modifies_expansion_enabled=True,
    )

    assert result["passed"] is True


def test_expected_any_accepts_semantically_equivalent_inflection() -> None:
    case = JourneyCase(
        key="unchanged",
        tags=["authority"],
        query="Was the rate changed?",
        anchors=[],
        expected_tokens=["10%"],
        expected_any=["not changed"],
    )
    message = _message(
        content="No, the rate did not change; it is still 10%.",
        metadata={
            "evidence_gate": {"sufficient": True},
            "web_search": {"status": "not_requested", "fallback_used": False},
        },
    )
    result = evaluate_case_result(
        case=case,
        message=message,
        anchor_mapping={},
        document_ids={},
        response_mode=ResponseMode.INDEXED_ONLY,
        modifies_expansion_enabled=True,
    )
    assert result["passed"] is True


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
        content=(
            "There is not enough evidence within the requested document scope to establish "
            "the current authoritative value."
        ),
        grounded=False,
        metadata={
            "retrieval_trace": {
                "candidates": [{"chunk_id": str(uuid.uuid4()), "document_id": str(outside_id)}],
                "retrieval_selected": [],
                "context_selected": [],
            },
            "current_authority": {"status": "expanded"},
            "scope_current_authority": {"status": "unavailable_within_hard_scope"},
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


def test_scope_isolation_requires_scoped_anchor_and_allows_safe_refusal() -> None:
    scoped_id = uuid.uuid4()
    scoped_chunk_id = uuid.uuid4()
    case = JourneyCase(
        key="scope",
        tags=["scope", "authority"],
        query="Current rate?",
        anchors=["old"],
        document_scope="old",
        mode="scope_isolation",
    )
    refused = _message(
        content=(
            "There is not enough evidence within the requested document scope to establish "
            "the current authoritative value."
        ),
        grounded=False,
        insufficient_evidence_reason=InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD,
        metadata={
            "retrieval_trace": {
                "candidates": [{"chunk_id": str(scoped_chunk_id), "document_id": str(scoped_id)}],
                "retrieval_selected": [
                    {"chunk_id": str(scoped_chunk_id), "document_id": str(scoped_id)}
                ],
                "context_selected": [],
            },
            "current_authority": {"status": "suppressed_document_scope"},
            "scope_current_authority": {"status": "unavailable_within_hard_scope"},
            "evidence_gate": {"sufficient": False, "generation_ran": False},
            "web_search": {"status": "suppressed_scoped_request", "fallback_used": False},
        },
    )

    passed = evaluate_case_result(
        case=case,
        message=refused,
        anchor_mapping={"old": [scoped_chunk_id]},
        document_ids={"old": scoped_id},
        response_mode=ResponseMode.INDEXED_THEN_WEB,
        modifies_expansion_enabled=True,
    )
    assert passed["passed"] is True
    assert passed["retrieval"]["recall"] == 1.0

    missing_anchor = _message(
        content=(
            "There is not enough evidence within the requested document scope to establish "
            "the current authoritative value."
        ),
        grounded=False,
        metadata={
            "retrieval_trace": {
                "candidates": [{"chunk_id": str(uuid.uuid4()), "document_id": str(scoped_id)}],
                "retrieval_selected": [],
                "context_selected": [],
            },
            "current_authority": {"status": "suppressed_document_scope"},
            "scope_current_authority": {"status": "unavailable_within_hard_scope"},
            "evidence_gate": {"sufficient": False},
            "web_search": {"status": "suppressed_scoped_request", "fallback_used": False},
        },
    )
    failed = evaluate_case_result(
        case=case,
        message=missing_anchor,
        anchor_mapping={"old": [scoped_chunk_id]},
        document_ids={"old": scoped_id},
        response_mode=ResponseMode.INDEXED_THEN_WEB,
        modifies_expansion_enabled=True,
    )
    assert {failure["stage"] for failure in failed["failures"]} == {"retrieval"}


def test_historical_authority_allows_raw_future_candidates_but_not_final_evidence() -> None:
    old_document_id = uuid.uuid4()
    future_document_id = uuid.uuid4()
    old_chunk_id = uuid.uuid4()
    case = JourneyCase(
        key="historical",
        tags=["authority", "historical"],
        query="What applied in 2024?",
        anchors=["old"],
        expected_tokens=["15%"],
    )
    message = _message(
        content="The historical rate was 15%.",
        metadata={
            "retrieval_trace": {
                "candidates": [
                    {"chunk_id": str(uuid.uuid4()), "document_id": str(future_document_id)},
                    {"chunk_id": str(old_chunk_id), "document_id": str(old_document_id)},
                ],
                "retrieval_selected": [
                    {"chunk_id": str(old_chunk_id), "document_id": str(old_document_id)}
                ],
                "context_selected": [
                    {"chunk_id": str(old_chunk_id), "document_id": str(old_document_id)}
                ],
            },
            "evidence_gate": {"sufficient": True},
            "web_search": {"status": "not_requested", "fallback_used": False},
        },
    )

    result = evaluate_case_result(
        case=case,
        message=message,
        anchor_mapping={"old": [old_chunk_id]},
        document_ids={"tax_2023": old_document_id},
        response_mode=ResponseMode.INDEXED_ONLY,
        modifies_expansion_enabled=True,
    )

    assert all(failure["stage"] != "authority" for failure in result["failures"])


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


async def test_ensure_indexed_processes_every_fixture_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.document import DocumentStatus
    from app.models.index_build import IndexBuildState

    tax_id = uuid.uuid4()
    finance_id = uuid.uuid4()
    documents = {
        tax_id: SimpleNamespace(id=tax_id, status=DocumentStatus.CHUNKED),
        finance_id: SimpleNamespace(id=finance_id, status=DocumentStatus.CHUNKED),
    }
    embed_calls: list[uuid.UUID] = []
    index_calls: list[uuid.UUID] = []
    build = SimpleNamespace(
        id=uuid.uuid4(),
        state=IndexBuildState.ACTIVE,
        document_count=0,
        chunk_count=2,
        vector_count=2,
        keyword_count=2,
        embedding_set_version=2,
        configuration_hash="cfg",
        corpus_fingerprint="fp",
    )

    class _Indexing:
        async def enqueue_embed(self, document_id: uuid.UUID) -> None:
            embed_calls.append(document_id)
            documents[document_id].status = DocumentStatus.EMBEDDED
            build.document_count = len(documents)

        async def enqueue_index(self, document_id: uuid.UUID) -> None:
            index_calls.append(document_id)
            documents[document_id].status = DocumentStatus.READY
            build.document_count = len(documents)

    class _DocumentRepository:
        def __init__(self, _session: object, _project_id: uuid.UUID) -> None:
            del _session, _project_id

        async def get_by_id(self, document_id: uuid.UUID, include_deleted: bool = False) -> object:
            del include_deleted
            return documents[document_id]

    class _IndexBuildRepository:
        def __init__(self, _session: object, _project_id: uuid.UUID) -> None:
            del _session, _project_id

        async def get_active(self) -> object | None:
            return build if build.document_count else None

    monkeypatch.setattr(
        "app.modules.knowledge.repositories.document_repository.DocumentRepository",
        _DocumentRepository,
    )
    monkeypatch.setattr(
        "app.modules.retrieval.repositories.index_build_repository.IndexBuildRepository",
        _IndexBuildRepository,
    )
    monkeypatch.setattr(
        "app.composition.retrieval.build_indexing_service",
        lambda **_kwargs: _Indexing(),
    )

    class _SessionFactory:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

        def __call__(self) -> _SessionFactory:
            return self

    result = await _ensure_indexed(
        _SessionFactory(),
        project_id=uuid.uuid4(),
        document_ids={"tax_2023": tax_id, "finance_2026": finance_id},
        settings=SimpleNamespace(),
    )

    assert embed_calls == [tax_id, finance_id]
    assert index_calls == [tax_id, finance_id]
    assert result["document_count"] == 2


async def test_ensure_indexed_fails_clearly_for_unsupported_indexing_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.document import DocumentStatus

    failed_id = uuid.uuid4()
    ready_id = uuid.uuid4()
    documents = {
        failed_id: SimpleNamespace(id=failed_id, status=DocumentStatus.FAILED),
        ready_id: SimpleNamespace(id=ready_id, status=DocumentStatus.READY),
    }

    class _DocumentRepository:
        def __init__(self, _session: object, _project_id: uuid.UUID) -> None:
            del _session, _project_id

        async def get_by_id(self, document_id: uuid.UUID, include_deleted: bool = False) -> object:
            del include_deleted
            return documents[document_id]

    class _IndexBuildRepository:
        def __init__(self, _session: object, _project_id: uuid.UUID) -> None:
            del _session, _project_id

        async def get_active(self) -> None:
            return None

    monkeypatch.setattr(
        "app.modules.knowledge.repositories.document_repository.DocumentRepository",
        _DocumentRepository,
    )
    monkeypatch.setattr(
        "app.modules.retrieval.repositories.index_build_repository.IndexBuildRepository",
        _IndexBuildRepository,
    )

    class _SessionFactory:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

        def __call__(self) -> _SessionFactory:
            return self

    with pytest.raises(JourneyError, match="indexing mode"):
        await _ensure_indexed(
            _SessionFactory(),
            project_id=uuid.uuid4(),
            document_ids={"tax_2023": failed_id, "finance_2026": ready_id},
            settings=SimpleNamespace(),
        )
