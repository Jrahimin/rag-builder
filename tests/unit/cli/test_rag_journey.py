"""Focused tests for the small local RAG journey contract."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.cli.rag_journey import (
    DEFAULT_FIXTURE,
    SAFE_CONFIG_KEYS,
    EvidenceAnchor,
    JourneyCase,
    JourneyError,
    JourneyManifest,
    JourneySource,
    RuntimeChunk,
    _await_document_purge,
    _comparison_summary,
    _ensure_indexed,
    _preflight_default_organization,
    _project_ai_leaf_paths,
    aggregate_results,
    build_project_config,
    build_translation_comparison,
    case_language_bucket,
    classify_translation_verdict,
    evaluate_case_result,
    load_manifest,
    normalize_text,
    parse_config_assignment,
    render_summary,
    resolve_evidence_anchors,
    sanitize_diagnostics,
    source_purge_order,
    tag_aggregates,
    translation_changed_retrieval_outcome,
)
from app.cli.rag_journey_cli import _options, _parser
from app.core.config import ResponseMode
from app.modules.conversations.schemas.message import (
    CitationSourceKind,
    ClaimVerification,
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


def test_tax_v1_manifest_keeps_original_cases_and_adds_focused_authority_coverage() -> None:
    manifest = load_manifest()

    assert manifest.key == "tax_v1"
    assert len(manifest.cases) == 21
    assert {
        "eligible_investments_scoped",
        "current_rebate_calculation",
        "historical_rebate_rate",
        "current_rebate_bangla",
        "current_rebate_banglish",
        "stale_rebate_correction",
        "current_threshold",
        "unchanged_source_tax",
        "hard_document_scope_authority",
        "unknown_lunar_rule",
    }.issubset({case.key for case in manifest.cases})
    assert {
        "mixed_document_bangla_retrieval",
        "mixed_document_code_switched_retrieval",
    }.issubset({case.key for case in manifest.cases})
    assert {"multilingual", "authority", "scope", "refusal"}.issubset(
        {tag for case in manifest.cases for tag in case.tags}
    )


def test_tax_v1_fixture_requires_all_eligible_categories_and_stale_correction() -> None:
    manifest = load_manifest()
    cases = {case.key: case for case in manifest.cases}

    eligible = cases["eligible_investments_scoped"]
    assert eligible.expected_tokens == []
    assert eligible.expected_token_groups == [
        ["approved savings certificates", "savings certificates"],
        ["approved retirement contributions", "retirement contributions"],
        [
            "approved life-insurance premiums",
            "life-insurance premiums",
            "life insurance premiums",
        ],
    ]
    declared = cases["declared_investment_75000"]
    assert declared.user_parameter_tokens == ["75000"]
    assert declared.required_anchor_groups == [["rebate_rate_2026"]]
    assert "declared_amount_2024_bn" not in declared.anchors
    bilingual = cases["historical_rebate_bilingual"]
    assert "historical_rebate_rate_2024_bn" in bilingual.required_anchor_groups[0]
    assert bilingual.prohibited_final_sources == ["finance_2026", "finance_2027"]
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
    assert finance.modified_provisions["tax_2023_bn"] == [
        "ধারা ১০ — করমুক্ত সীমা",
        "ধারা ২১ — বিনিয়োগ রিবেটের হার",
        "ধারা \u09ea\u09e6 — ব্যক্তিগত করের উদাহরণ",
    ]
    rules = next(source for source in manifest.sources if source.key == "tax_rules_2024_bn")
    assert rules.modifies == []
    guidance = next(source for source in manifest.sources if source.key == "tax_guidance_2025")
    assert guidance.modifies == []
    assert guidance.modified_provisions == {}
    amendment = next(source for source in manifest.sources if source.key == "finance_2027")
    assert amendment.modifies == ["finance_2026"]
    assert (
        "Section 10 — Revised Tax-Free Threshold"
        not in amendment.modified_provisions["finance_2026"]
    )
    assert (
        "Section 15 — Savings Certificate Source Tax"
        not in amendment.modified_provisions["finance_2026"]
    )
    historical = cases["historical_rebate_rate"]
    assert historical.prohibited_final_sources == ["finance_2027"]
    assert "historical_rebate_rate_2026" in historical.required_anchor_groups[0]
    assert "historical_rebate_rate_2024_bn" in historical.required_anchor_groups[0]
    composed = cases["current_2027_rebate_and_threshold"]
    assert composed.required_anchor_groups == [["rebate_rate_2027"], ["threshold_2026"]]
    amendment_path = DEFAULT_FIXTURE.parent / "corpus" / amendment.filename
    amendment_text = amendment_path.read_text(encoding="utf-8")
    assert "400,000" not in amendment_text
    assert "400000" not in amendment_text
    assert "still-effective 2026" in amendment_text


def test_tax_v1_fixture_sources_keep_intended_chunking_paths() -> None:
    from app.core.config import ChunkingConfig, ChunkingStrategy
    from app.modules.knowledge.services.chunking.chunk_strategy_selector_service import (
        ChunkStrategySelectorService,
    )
    from app.modules.knowledge.services.chunking.sentence_similarity_service import (
        HashSentenceSimilarityService,
    )
    from app.modules.knowledge.services.chunking.structure_analyzer_service import (
        StructureAnalyzerService,
    )
    from app.modules.knowledge.services.chunking_service import ChunkingService
    from app.platform.domain.language_detection import detect_language
    from app.platform.providers.implementations.plain_text_parser import PlainTextParserProvider

    manifest = load_manifest()
    markdown_source_keys = {
        "tax_2023",
        "tax_2023_bn",
        "tax_rules_2024_bn",
        "finance_2026",
        "finance_2027",
    }
    mixed_source_keys = {"tax_guidance_2025"}
    assert {source.key for source in manifest.sources} == markdown_source_keys | mixed_source_keys
    parser = PlainTextParserProvider()
    config = ChunkingConfig()
    analyzer = StructureAnalyzerService(config=config)
    selector = ChunkStrategySelectorService()
    service = ChunkingService(config=config, similarity_service=HashSentenceSimilarityService())
    corpus = DEFAULT_FIXTURE.parent / "corpus"
    chunks: list[RuntimeChunk] = []
    for source in manifest.sources:
        path = corpus / source.filename
        parsed = parser.parse(
            data=path.read_bytes(),
            filename=source.filename,
            content_type="text/markdown",
        )
        detected = detect_language(path.read_text(encoding="utf-8"))
        analysis = analyzer.analyze(parsed)
        strategy = selector.select(parsed, analysis, config)
        text_chunks, run_metadata = asyncio.run(service.split_document(parsed))
        if source.key in mixed_source_keys:
            assert detected.is_mixed is True, source.key
            assert parsed.language == "mixed", source.key
            assert parsed.structure_hints.get("is_mixed") is True, source.key
            assert strategy is ChunkingStrategy.SEMANTIC, source.key
            assert run_metadata.strategy_used is ChunkingStrategy.SEMANTIC, source.key
            assert run_metadata.semantic_refinement_used is True, source.key
            assert text_chunks
            assert all(
                chunk.chunk_metadata.get("strategy_used") == "semantic" for chunk in text_chunks
            )
        else:
            assert detected.is_mixed is False, source.key
            assert strategy is ChunkingStrategy.MARKDOWN, source.key
            assert run_metadata.strategy_used is ChunkingStrategy.MARKDOWN, source.key
            assert len(text_chunks) > 1, source.key
        document_id = uuid.uuid4()
        chunks.extend(
            RuntimeChunk(
                id=uuid.uuid4(),
                document_id=document_id,
                source_key=source.key,
                chunk_index=index,
                content=chunk.content,
                metadata=dict(chunk.chunk_metadata or {}),
            )
            for index, chunk in enumerate(text_chunks)
        )

    mapping = resolve_evidence_anchors(manifest.anchors, chunks)
    assert mapping["savings_evidence_2024_bn"] != mapping["declared_amount_2024_bn"]
    assert mapping["verification_reference_2025"]
    assert mapping["review_window_2025"]
    mixed_anchors = [anchor for anchor in manifest.anchors if anchor.source == "tax_guidance_2025"]
    assert mixed_anchors
    assert all(anchor.section is None for anchor in mixed_anchors)
    structured_anchors = [
        anchor for anchor in manifest.anchors if anchor.source in markdown_source_keys
    ]
    assert structured_anchors
    assert all(anchor.section for anchor in structured_anchors)


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


def test_phrase_only_anchor_matches_content_without_headings() -> None:
    chunk_id = uuid.uuid4()
    document_id = uuid.uuid4()
    anchor = EvidenceAnchor(
        key="verification",
        source="guidance",
        phrases=["VR-2025-APE"],
    )
    chunks = [
        RuntimeChunk(
            id=chunk_id,
            document_id=document_id,
            source_key="guidance",
            chunk_index=0,
            content="The system stores verification reference VR-2025-APE for workflow tracking.",
            metadata={},
        ),
        RuntimeChunk(
            id=uuid.uuid4(),
            document_id=document_id,
            source_key="guidance",
            chunk_index=1,
            content="This procedural note does not change the rebate rate.",
            metadata={"section_title": "Other notes"},
        ),
    ]

    assert resolve_evidence_anchors([anchor], chunks) == {"verification": [chunk_id]}


def test_phrase_only_anchor_accepts_all_matching_semantic_chunks() -> None:
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    document_id = uuid.uuid4()
    anchor = EvidenceAnchor(
        key="verification",
        source="guidance",
        phrases=["VR-2025-APE"],
    )
    chunks = [
        RuntimeChunk(
            id=chunk_id,
            document_id=document_id,
            source_key="guidance",
            chunk_index=index,
            content=content,
            metadata={},
        )
        for index, (chunk_id, content) in enumerate(
            (
                (first_id, "Verification reference: VR-2025-APE"),
                (second_id, "The unique stored fact is VR-2025-APE."),
            )
        )
    ]

    assert resolve_evidence_anchors([anchor], chunks) == {"verification": [first_id, second_id]}


def test_tax_v1_mixed_document_queries_have_intended_language_profiles() -> None:
    from app.platform.domain.language_detection import detect_query_language_profile

    cases = {case.key: case for case in load_manifest().cases}
    bangla = detect_query_language_profile(cases["mixed_document_bangla_retrieval"].query)
    switched = detect_query_language_profile(cases["mixed_document_code_switched_retrieval"].query)

    assert bangla.profile == "bn"
    assert bangla.is_mixed is False
    assert switched.profile == "mixed"
    assert switched.is_mixed is True
    assert cases["mixed_document_bangla_retrieval"].content_match_anchors == [
        "verification_reference_2025"
    ]
    assert cases["mixed_document_code_switched_retrieval"].content_match_anchors == []


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
    key, value = parse_config_assignment("behavior.translation_policy=disabled")

    assert (key, value) == ("behavior.translation_policy", "disabled")
    config = build_project_config(
        {
            key: value,
            "behavior.response_mode": "indexed_then_web",
            "behavior.grounding_assurance": "balanced",
        }
    )
    assert config.behavior.translation_policy.value == "disabled"
    assert config.behavior.response_mode is ResponseMode.INDEXED_THEN_WEB
    assert config.behavior.grounding_assurance.value == "balanced"


def test_empty_baseline_inherits_deployment_config_without_implicit_policy_overrides() -> None:
    config = build_project_config({})
    payload = config.model_dump(exclude_none=True)

    assert payload["behavior"]["translation_policy"] == "inherit"
    assert payload["execution"] == {}

    with pytest.raises(JourneyError, match="Unsafe or unknown"):
        build_project_config({"source_policy_mode": "enforce"})


def test_test_lab_materializes_candidate_profile_without_persisting_candidate_identity() -> None:
    config = build_project_config({"execution.profile_id": "economy@v1"})

    assert config.execution.profile_id is None
    assert config.execution.semantic_candidate_top_k == 30
    assert config.execution.rerank_candidate_window == 15
    assert config.execution.retrieval_top_k == 8


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


def test_source_purge_order_for_tax_v1_modifier_chain() -> None:
    manifest = JourneyManifest.model_validate_json(DEFAULT_FIXTURE.read_text(encoding="utf-8"))
    order = source_purge_order(manifest.sources)
    assert order.index("finance_2027") < order.index("finance_2026")
    assert order.index("finance_2026") < order.index("tax_2023")
    assert order.index("finance_2026") < order.index("tax_2023_bn")


async def test_await_document_purge_returns_when_job_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.job_run import JobRun, JobState, JobType

    project_id = uuid.uuid4()
    job_id = uuid.uuid4()
    run = JobRun(
        id=job_id,
        project_id=project_id,
        job_type=JobType.DOCUMENT_PURGE,
        state=JobState.SUCCEEDED,
        stage="purged",
        progress=100,
        payload={},
        idempotency_key="purge:test",
        configuration_snapshot_id=uuid.uuid4(),
    )

    class _SessionFactory:
        def __call__(self) -> Any:
            return self

        async def __aenter__(self) -> Any:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class _Service:
        async def get_detail(self, _job_id: uuid.UUID) -> Any:
            return SimpleNamespace(run=run)

        async def dispatch(self, _job_id: uuid.UUID) -> None:
            raise AssertionError("dispatch should not run for succeeded jobs")

        async def dispatch_next(self) -> bool:
            raise AssertionError("dispatch_next should not run for succeeded jobs")

    monkeypatch.setattr(
        "app.composition.jobs.build_job_service",
        lambda **_kwargs: _Service(),
    )

    await _await_document_purge(
        _SessionFactory(),
        project_id=project_id,
        job_id=job_id,
        settings=SimpleNamespace(),
    )


async def test_await_document_purge_raises_when_job_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.job_run import JobRun, JobState, JobType

    project_id = uuid.uuid4()
    job_id = uuid.uuid4()
    run = JobRun(
        id=job_id,
        project_id=project_id,
        job_type=JobType.DOCUMENT_PURGE,
        state=JobState.FAILED,
        stage="failed",
        progress=0,
        payload={},
        idempotency_key="purge:test",
        configuration_snapshot_id=uuid.uuid4(),
        failure_code="provider_rate_limit_error",
        failure_message="rate limited",
    )

    class _SessionFactory:
        def __call__(self) -> Any:
            return self

        async def __aenter__(self) -> Any:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class _Service:
        async def get_detail(self, _job_id: uuid.UUID) -> Any:
            return SimpleNamespace(run=run)

    monkeypatch.setattr(
        "app.composition.jobs.build_job_service",
        lambda **_kwargs: _Service(),
    )

    with pytest.raises(JourneyError, match="provider_rate_limit_error"):
        await _await_document_purge(
            _SessionFactory(),
            project_id=project_id,
            job_id=job_id,
            settings=SimpleNamespace(),
        )


def test_cli_rejects_more_than_one_comparison_variant() -> None:
    args = _parser().parse_args(
        [
            "--compare",
            "behavior.translation_policy=disabled",
            "--compare",
            "behavior.translation_policy=enabled",
        ]
    )

    with pytest.raises(JourneyError, match="only one --compare"):
        _options(args, configured_job_backend="taskiq")


def test_cli_compare_translation_is_exclusive_with_compare() -> None:
    args = _parser().parse_args(
        ["--compare-translation", "--compare", "behavior.translation_policy=disabled"]
    )

    with pytest.raises(JourneyError, match="either --compare-translation or --compare"):
        _options(args, configured_job_backend="taskiq")


def test_cli_compare_translation_sets_query_time_off_variant() -> None:
    options = _options(
        _parser().parse_args(["--compare-translation"]),
        configured_job_backend="taskiq",
    )

    assert options.compare_translation is True
    assert options.comparison == ("behavior.translation_policy", "disabled")


def _message(
    *,
    content: str,
    metadata: dict[str, object],
    grounded: bool = True,
    insufficient_evidence_reason: InsufficientEvidenceReason | None = None,
    claims: list[object] | None = None,
) -> object:
    return SimpleNamespace(
        content=content,
        metadata=metadata,
        citations=[],
        claims=claims or [],
        grounded=grounded,
        insufficient_evidence_reason=insufficient_evidence_reason,
        source_provenance=SourceProvenance.NONE,
        retrieval_latency_ms=2,
        provider_latency_ms=3,
        total_latency_ms=5,
    )


def _knowledge_claim(*, chunk_id: uuid.UUID, document_id: uuid.UUID, text: str) -> object:
    return SimpleNamespace(
        claim_id="claim-1",
        text=text,
        grounded=True,
        verification=ClaimVerification.SUPPORTED,
        evidence=[
            SimpleNamespace(
                source_kind=CitationSourceKind.KNOWLEDGE,
                chunk_id=chunk_id,
                document_id=document_id,
            )
        ],
    )


def _answerable_evidence_message(
    *,
    content: str,
    retrieved: list[dict[str, str]],
    admitted: list[dict[str, str]],
    claim_chunk_id: uuid.UUID,
    claim_document_id: uuid.UUID,
    citation_chunk_id: uuid.UUID | None = None,
    citation_document_id: uuid.UUID | None = None,
) -> object:
    message = _message(
        content=content,
        claims=[
            _knowledge_claim(
                chunk_id=claim_chunk_id,
                document_id=claim_document_id,
                text=content,
            )
        ],
        metadata={
            "retrieval_trace": {
                "retrieval_selected": retrieved,
                "context_selected": admitted,
            },
            "evidence_gate": {"sufficient": True},
            "web_search": {"status": "not_requested", "fallback_used": False},
        },
    )
    message.citations = [
        SimpleNamespace(
            source_kind=CitationSourceKind.KNOWLEDGE,
            chunk_id=citation_chunk_id or claim_chunk_id,
            document_id=citation_document_id or claim_document_id,
            filename="guidance.md",
            source_revision_id=None,
            web_url=None,
        )
    ]
    return message


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


def test_expected_token_groups_accept_equivalent_wording() -> None:
    case = JourneyCase(
        key="eligible",
        tags=["factual"],
        query="Which investments are eligible?",
        anchors=[],
        expected_token_groups=[
            ["approved savings certificates", "savings certificates"],
            ["approved retirement contributions", "retirement contributions"],
        ],
    )
    message = _message(
        content="Eligible investments include savings certificates and retirement contributions.",
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


def test_expected_token_groups_still_require_each_fact_family() -> None:
    case = JourneyCase(
        key="eligible",
        tags=["factual"],
        query="Which investments are eligible?",
        anchors=[],
        expected_token_groups=[
            ["approved savings certificates", "savings certificates"],
            ["approved retirement contributions", "retirement contributions"],
        ],
    )
    message = _message(
        content="Eligible investments include savings certificates.",
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

    assert result["passed"] is False
    assert any("retirement contributions" in item["message"] for item in result["failures"])


def test_user_parameter_tokens_need_not_be_cited_from_the_knowledge_base() -> None:
    rate_id = uuid.uuid4()
    amount_id = uuid.uuid4()
    document_id = uuid.uuid4()
    case = JourneyCase(
        key="declared",
        tags=["calculation"],
        query="Use my amount of 75,000.",
        anchors=["rebate_rate"],
        required_anchor_groups=[["rebate_rate"]],
        user_parameter_tokens=["75000"],
        expected_tokens=["10%", "75000", "7500"],
    )
    claims = [
        SimpleNamespace(
            claim_id="claim-1",
            text="The rebate is 7,500.",
            grounded=True,
            verification=ClaimVerification.SUPPORTED,
            evidence=[
                SimpleNamespace(
                    source_kind=CitationSourceKind.KNOWLEDGE,
                    chunk_id=rate_id,
                    document_id=document_id,
                )
            ],
        )
    ]
    identities = [{"chunk_id": str(rate_id), "document_id": str(document_id)}]
    message = _message(
        content="Using your 75000, the 10% rebate is 7500.",
        claims=claims,
        metadata={
            "retrieval_trace": {
                "retrieval_selected": [
                    *identities,
                    {"chunk_id": str(amount_id), "document_id": str(document_id)},
                ],
                "context_selected": identities,
            },
            "evidence_gate": {"sufficient": True},
            "web_search": {"status": "not_requested", "fallback_used": False},
        },
    )
    message.citations = [
        SimpleNamespace(
            source_kind=CitationSourceKind.KNOWLEDGE,
            chunk_id=rate_id,
            document_id=document_id,
            filename="finance.pdf",
            source_revision_id=None,
            web_url=None,
        )
    ]

    result = evaluate_case_result(
        case=case,
        message=message,
        anchor_mapping={"rebate_rate": [rate_id], "declared_amount": [amount_id]},
        document_ids={},
        response_mode=ResponseMode.INDEXED_ONLY,
        modifies_expansion_enabled=True,
    )

    assert result["passed"] is True


def test_rerank_timeout_is_provider_degradation_not_a_semantic_failure() -> None:
    case = JourneyCase(
        key="current",
        tags=["authority"],
        query="What is the rate?",
        anchors=[],
        expected_tokens=["10%"],
    )
    message = _message(
        content="The current rebate rate is 10%.",
        metadata={
            "retrieval_trace": {
                "rerank": {"status": "unavailable", "failure_reason": "timeout"},
            },
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
    summary = aggregate_results([result])

    assert result["passed"] is True
    assert result["provider_degradation"] == {
        "component": "rerank",
        "status": "unavailable",
        "failure_reason": "timeout",
    }
    assert summary["provider_degradation"]["rerank_unavailable_count"] == 1
    assert summary["provider_degradation"]["by_failure_reason"] == {"timeout": 1}
    assert summary["correctness"]["failed"] == 0
    assert summary["failure_counts"]["admission_grounding"] == 0


def test_empty_context_after_admission_is_context_selection_not_admission() -> None:
    case = JourneyCase(
        key="current",
        tags=["authority"],
        query="What is the rate?",
        anchors=[],
    )
    message = _message(
        content="There is not enough indexed evidence to answer from the knowledge base.",
        grounded=False,
        insufficient_evidence_reason=InsufficientEvidenceReason.AUTHORITY_CONTEXT_EMPTY,
        metadata={
            "retrieval_trace": {"context_selected": []},
            "evidence_gate": {
                "sufficient": False,
                "reason": "authority_context_empty",
                "failure_stage": "context_selection",
                "generation_ran": False,
                "candidate_wise": {
                    "admitted_count": 1,
                    "assessments": [
                        {"chunk_id": str(uuid.uuid4()), "passed": True},
                    ],
                },
            },
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
    summary = aggregate_results([result])

    assert result["passed"] is False
    assert {failure["stage"] for failure in result["failures"]} == {"context_selection"}
    assert summary["failure_counts"]["context_selection"] >= 1
    assert summary["failure_counts"]["admission_grounding"] == 0


def test_codeswitch_case_requires_query_language_profile_diagnostics() -> None:
    case = JourneyCase(
        key="mixed_document_code_switched_retrieval",
        tags=["multilingual", "mixed_document", "codeswitch"],
        query="কর নির্দেশিকায় verification reference code কী?",
        anchors=[],
        expected_tokens=["VR-2025-APE"],
    )
    missing = _message(
        content="The stored verification reference is VR-2025-APE.",
        metadata={
            "retrieval_trace": {
                "retrieval_selected": [],
                "context_selected": [],
                "translation": {},
            },
            "evidence_gate": {"sufficient": True},
            "web_search": {"status": "not_requested", "fallback_used": False},
        },
    )
    failed = evaluate_case_result(
        case=case,
        message=missing,
        anchor_mapping={},
        document_ids={},
        response_mode=ResponseMode.INDEXED_ONLY,
        modifies_expansion_enabled=True,
    )
    assert failed["passed"] is False
    assert any(
        failure["stage"] == "retrieval" and "language/translation diagnostics" in failure["message"]
        for failure in failed["failures"]
    )

    present = _message(
        content="The stored verification reference is VR-2025-APE.",
        metadata={
            "retrieval_trace": {
                "retrieval_selected": [],
                "context_selected": [],
                "translation": {
                    "query_language_profile": "mixed",
                    "status": "skipped",
                    "romanized_or_codeswitched": False,
                },
            },
            "evidence_gate": {"sufficient": True},
            "web_search": {"status": "not_requested", "fallback_used": False},
        },
    )
    passed = evaluate_case_result(
        case=case,
        message=present,
        anchor_mapping={},
        document_ids={},
        response_mode=ResponseMode.INDEXED_ONLY,
        modifies_expansion_enabled=True,
    )
    assert passed["passed"] is True
    assert passed["translation"]["query_language_profile"] == "mixed"


def _verification_reference_chunks() -> tuple[
    uuid.UUID,
    uuid.UUID,
    uuid.UUID,
    uuid.UUID,
    uuid.UUID,
    uuid.UUID,
    uuid.UUID,
    list[RuntimeChunk],
    EvidenceAnchor,
]:
    document_id = uuid.uuid4()
    other_document_id = uuid.uuid4()
    phrase_id = uuid.uuid4()
    neighbor_id = uuid.uuid4()
    later_phrase_id = uuid.uuid4()
    distant_id = uuid.uuid4()
    other_id = uuid.uuid4()
    chunks = [
        RuntimeChunk(
            id=phrase_id,
            document_id=document_id,
            source_key="tax_guidance_2025",
            chunk_index=1,
            content="Verification reference: VR-2025-APE",
            metadata={},
        ),
        RuntimeChunk(
            id=neighbor_id,
            document_id=document_id,
            source_key="tax_guidance_2025",
            chunk_index=2,
            content="This reference is only for workflow tracking.",
            metadata={},
        ),
        RuntimeChunk(
            id=later_phrase_id,
            document_id=document_id,
            source_key="tax_guidance_2025",
            chunk_index=5,
            content="Regression fact: verification reference VR-2025-APE",
            metadata={},
        ),
        RuntimeChunk(
            id=distant_id,
            document_id=document_id,
            source_key="tax_guidance_2025",
            chunk_index=8,
            content="The additional-document review window is 14 calendar days.",
            metadata={},
        ),
        RuntimeChunk(
            id=other_id,
            document_id=other_document_id,
            source_key="finance_2026",
            chunk_index=1,
            content="The rebate rate is 10%.",
            metadata={},
        ),
    ]
    anchor = EvidenceAnchor(
        key="verification_reference_2025",
        source="tax_guidance_2025",
        phrases=["VR-2025-APE"],
    )
    return (
        document_id,
        other_document_id,
        phrase_id,
        neighbor_id,
        later_phrase_id,
        distant_id,
        other_id,
        chunks,
        anchor,
    )


def test_bangla_mixed_document_accepts_overlapping_or_phrase_containing_2025_evidence() -> None:
    (
        document_id,
        _other_document_id,
        phrase_id,
        neighbor_id,
        later_phrase_id,
        _distant_id,
        _other_id,
        chunks,
        anchor,
    ) = _verification_reference_chunks()
    case = JourneyCase(
        key="mixed_document_bangla_retrieval",
        tags=["multilingual", "mixed_document"],
        query="যাচাইয়ের স্বতন্ত্র রেফারেন্স কী?",
        anchors=["verification_reference_2025"],
        required_anchor_groups=[["verification_reference_2025"]],
        content_match_anchors=["verification_reference_2025"],
        expected_tokens=["VR-2025-APE"],
    )
    identities = [
        {"chunk_id": str(phrase_id), "document_id": str(document_id)},
        {"chunk_id": str(neighbor_id), "document_id": str(document_id)},
        {"chunk_id": str(later_phrase_id), "document_id": str(document_id)},
    ]
    overlapping = evaluate_case_result(
        case=case,
        message=_answerable_evidence_message(
            content="The stored verification reference is VR-2025-APE.",
            retrieved=identities,
            admitted=identities,
            claim_chunk_id=neighbor_id,
            claim_document_id=document_id,
        ),
        anchor_mapping={"verification_reference_2025": [phrase_id]},
        document_ids={"tax_guidance_2025": document_id},
        response_mode=ResponseMode.INDEXED_ONLY,
        modifies_expansion_enabled=True,
        chunks=chunks,
        anchors=[anchor],
    )
    later_phrase = evaluate_case_result(
        case=case,
        message=_answerable_evidence_message(
            content="The stored verification reference is VR-2025-APE.",
            retrieved=identities,
            admitted=identities,
            claim_chunk_id=later_phrase_id,
            claim_document_id=document_id,
        ),
        anchor_mapping={"verification_reference_2025": [phrase_id]},
        document_ids={"tax_guidance_2025": document_id},
        response_mode=ResponseMode.INDEXED_ONLY,
        modifies_expansion_enabled=True,
        chunks=chunks,
        anchors=[anchor],
    )

    assert overlapping["passed"] is True
    assert later_phrase["passed"] is True


def test_bangla_mixed_document_still_requires_2025_evidence_not_answer_text() -> None:
    (
        document_id,
        other_document_id,
        phrase_id,
        neighbor_id,
        _later_phrase_id,
        distant_id,
        other_id,
        chunks,
        anchor,
    ) = _verification_reference_chunks()
    bangla = JourneyCase(
        key="mixed_document_bangla_retrieval",
        tags=["multilingual", "mixed_document"],
        query="যাচাইয়ের স্বতন্ত্র রেফারেন্স কী?",
        anchors=["verification_reference_2025"],
        required_anchor_groups=[["verification_reference_2025"]],
        content_match_anchors=["verification_reference_2025"],
        expected_tokens=["VR-2025-APE"],
    )
    exact = JourneyCase(
        key="mixed_document_code_switched_retrieval",
        tags=["multilingual", "mixed_document", "codeswitch"],
        query="কর নির্দেশিকায় verification reference code কী?",
        anchors=["verification_reference_2025"],
        required_anchor_groups=[["verification_reference_2025"]],
        expected_tokens=["VR-2025-APE"],
    )
    phrase_identity = {"chunk_id": str(phrase_id), "document_id": str(document_id)}
    neighbor_identity = {"chunk_id": str(neighbor_id), "document_id": str(document_id)}
    distant_identity = {"chunk_id": str(distant_id), "document_id": str(document_id)}
    other_identity = {"chunk_id": str(other_id), "document_id": str(other_document_id)}
    retrieved = [phrase_identity, neighbor_identity]
    exact_overlap = evaluate_case_result(
        case=exact,
        message=_answerable_evidence_message(
            content="The stored verification reference is VR-2025-APE.",
            retrieved=retrieved,
            admitted=retrieved,
            claim_chunk_id=neighbor_id,
            claim_document_id=document_id,
            citation_chunk_id=phrase_id,
            citation_document_id=document_id,
        ),
        anchor_mapping={"verification_reference_2025": [phrase_id]},
        document_ids={"tax_guidance_2025": document_id},
        response_mode=ResponseMode.INDEXED_ONLY,
        modifies_expansion_enabled=True,
        chunks=chunks,
        anchors=[anchor],
    )
    distant = evaluate_case_result(
        case=bangla,
        message=_answerable_evidence_message(
            content="The stored verification reference is VR-2025-APE.",
            retrieved=[*retrieved, distant_identity],
            admitted=[*retrieved, distant_identity],
            claim_chunk_id=distant_id,
            claim_document_id=document_id,
        ),
        anchor_mapping={"verification_reference_2025": [phrase_id]},
        document_ids={"tax_guidance_2025": document_id},
        response_mode=ResponseMode.INDEXED_ONLY,
        modifies_expansion_enabled=True,
        chunks=chunks,
        anchors=[anchor],
    )
    other_source = evaluate_case_result(
        case=bangla,
        message=_answerable_evidence_message(
            content="The stored verification reference is VR-2025-APE.",
            retrieved=[*retrieved, other_identity],
            admitted=retrieved,
            claim_chunk_id=other_id,
            claim_document_id=other_document_id,
        ),
        anchor_mapping={"verification_reference_2025": [phrase_id]},
        document_ids={"tax_guidance_2025": document_id, "finance_2026": other_document_id},
        response_mode=ResponseMode.INDEXED_ONLY,
        modifies_expansion_enabled=True,
        chunks=chunks,
        anchors=[anchor],
    )
    answer_only = _message(
        content="The stored verification reference is VR-2025-APE.",
        claims=[],
        metadata={
            "retrieval_trace": {
                "retrieval_selected": retrieved,
                "context_selected": retrieved,
            },
            "evidence_gate": {"sufficient": True},
            "web_search": {"status": "not_requested", "fallback_used": False},
        },
    )

    answer_only_result = evaluate_case_result(
        case=bangla,
        message=answer_only,
        anchor_mapping={"verification_reference_2025": [phrase_id]},
        document_ids={"tax_guidance_2025": document_id},
        response_mode=ResponseMode.INDEXED_ONLY,
        modifies_expansion_enabled=True,
        chunks=chunks,
        anchors=[anchor],
    )

    assert exact_overlap["passed"] is False
    assert any(failure["stage"] == "citation" for failure in exact_overlap["failures"])
    assert distant["passed"] is False
    assert any(failure["stage"] == "citation" for failure in distant["failures"])
    assert other_source["passed"] is False
    assert any(failure["stage"] == "citation" for failure in other_source["failures"])
    assert answer_only_result["passed"] is False
    assert any(failure["stage"] == "citation" for failure in answer_only_result["failures"])


def test_required_anchor_groups_accept_alternatives_and_require_each_source_family() -> None:
    rate_id = uuid.uuid4()
    procedure_id = uuid.uuid4()
    document_id = uuid.uuid4()
    case = JourneyCase(
        key="mixed",
        tags=["mixed_source"],
        query="Rate and evidence?",
        anchors=[],
        required_anchor_groups=[["rate_en", "rate_bn"], ["procedure"]],
    )
    claims = [
        SimpleNamespace(
            claim_id="claim-1",
            text="The rate is 10%.",
            grounded=True,
            verification=ClaimVerification.SUPPORTED,
            evidence=[
                SimpleNamespace(
                    source_kind=CitationSourceKind.KNOWLEDGE,
                    chunk_id=rate_id,
                    document_id=document_id,
                )
            ],
        ),
        SimpleNamespace(
            claim_id="claim-2",
            text="Keep the certificate statement.",
            grounded=True,
            verification=ClaimVerification.SUPPORTED,
            evidence=[
                SimpleNamespace(
                    source_kind=CitationSourceKind.KNOWLEDGE,
                    chunk_id=procedure_id,
                    document_id=document_id,
                )
            ],
        ),
    ]
    identities = [
        {"chunk_id": str(rate_id), "document_id": str(document_id)},
        {"chunk_id": str(procedure_id), "document_id": str(document_id)},
    ]
    message = _message(
        content="The rate is 10%. Keep the certificate statement.",
        claims=claims,
        metadata={
            "retrieval_trace": {
                "retrieval_selected": identities,
                "context_selected": identities,
            },
            "evidence_gate": {"sufficient": True},
            "web_search": {"status": "not_requested", "fallback_used": False},
        },
    )

    result = evaluate_case_result(
        case=case,
        message=message,
        anchor_mapping={"rate_en": [], "rate_bn": [rate_id], "procedure": [procedure_id]},
        document_ids={},
        response_mode=ResponseMode.INDEXED_ONLY,
        modifies_expansion_enabled=True,
    )

    assert result["passed"] is True


def test_prohibited_final_source_and_answer_fact_are_reported() -> None:
    forbidden_document = uuid.uuid4()
    forbidden_chunk = uuid.uuid4()
    case = JourneyCase(
        key="future",
        tags=["authority"],
        query="As of 2026?",
        anchors=[],
        prohibited_final_sources=["future"],
        prohibited_answer_tokens=["12%"],
    )
    message = _message(
        content="The rate is 12%.",
        metadata={
            "retrieval_trace": {
                "context_selected": [
                    {
                        "chunk_id": str(forbidden_chunk),
                        "document_id": str(forbidden_document),
                    }
                ]
            },
            "evidence_gate": {"sufficient": True},
            "web_search": {"status": "not_requested", "fallback_used": False},
        },
    )

    result = evaluate_case_result(
        case=case,
        message=message,
        anchor_mapping={},
        document_ids={"future": forbidden_document},
        response_mode=ResponseMode.INDEXED_ONLY,
        modifies_expansion_enabled=True,
    )

    assert {failure["stage"] for failure in result["failures"]} == {
        "authority",
        "generation_refusal",
    }


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
        key="behavior.translation_policy",
    )

    assert report["affected_tags"] == ["multilingual"]
    assert report["tags"]["multilingual"]["affected"] is True
    assert report["tags"]["authority"]["affected"] is False


def test_language_buckets_follow_query_script_and_case_form() -> None:
    assert (
        case_language_bucket(
            key="current_rebate_bangla",
            tags=["multilingual"],
            query="বর্তমান নিয়মে রিবেট কত?",
        )
        == "bangla"
    )
    assert (
        case_language_bucket(
            key="current_rebate_banglish",
            tags=["multilingual"],
            query="Current niyome rebate koto?",
        )
        == "banglish_codeswitch"
    )
    assert (
        case_language_bucket(
            key="mixed_document_code_switched_retrieval",
            tags=["multilingual", "codeswitch"],
            query="কর নির্দেশিকায় verification reference code কী?",
        )
        == "banglish_codeswitch"
    )
    assert (
        case_language_bucket(
            key="historical_rebate_bilingual",
            tags=["multilingual"],
            query="On 1 January 2024 / ১ জানুয়ারি investment rebate rate কত ছিল?",
        )
        == "mixed_language"
    )
    assert (
        case_language_bucket(
            key="current_threshold",
            tags=["authority"],
            query="What is the current individual tax-free threshold?",
        )
        == "english"
    )


def _paired_case(
    *,
    key: str,
    passed: bool,
    recall: float,
    relevant_rank: int | None,
    relevant_ids: list[str],
    winning: str,
    admitted: list[str],
    total_ms: int,
    translation_ms: int | None = None,
    translation_applied: bool = False,
    contributed: bool = False,
    language_bucket: str = "bangla",
) -> dict[str, object]:
    item = {"chunk_id": winning, "document_id": "doc-1"}
    if contributed:
        item["translated_dense"] = {"rank": 1, "rrf": 0.03}
    return {
        "key": key,
        "tags": ["multilingual"],
        "language_bucket": language_bucket,
        "passed": passed,
        "retrieval": {
            "recall": recall,
            "reciprocal_rank": 0.0 if relevant_rank is None else 1.0 / relevant_rank,
            "ndcg": recall,
            "relevant_rank": relevant_rank,
            "relevant_retrieved_ids": relevant_ids,
            "selected": [item],
        },
        "admitted": [{"chunk_id": chunk_id} for chunk_id in admitted],
        "quality": {
            "expected_evidence_admitted": bool(admitted),
            "expected_evidence_admitted_count": len(admitted),
            "grounding_success": passed,
            "citation_correctness": passed,
            "generation_refusal_correctness": passed,
            "winning_chunk_id": winning,
            "winning_document_id": "doc-1",
            "translation_applied": translation_applied,
            "translation_contributed_to_winning": contributed,
            "translation_contributed_to_admitted": contributed,
        },
        "timings_ms": {
            "translation": translation_ms,
            "retrieval": 50,
            "rerank": 5,
            "grounding_and_context": 10,
            "generation": 40,
            "total": total_ms,
            "translation_share": (translation_ms / total_ms) if translation_ms else None,
        },
        "translation": {"status": "applied" if translation_applied else "disabled"},
    }


def test_translation_verdicts_and_retrieval_delta_are_evidence_based() -> None:
    required_on = _paired_case(
        key="current_rebate_bangla",
        passed=True,
        recall=1.0,
        relevant_rank=1,
        relevant_ids=["bn"],
        winning="bn",
        admitted=["bn"],
        total_ms=9000,
        translation_ms=4000,
        translation_applied=True,
        contributed=True,
    )
    required_off = _paired_case(
        key="current_rebate_bangla",
        passed=False,
        recall=0.0,
        relevant_rank=None,
        relevant_ids=[],
        winning="en-wrong",
        admitted=[],
        total_ms=3000,
    )
    helpful_on = _paired_case(
        key="current_rebate_banglish",
        passed=True,
        recall=1.0,
        relevant_rank=1,
        relevant_ids=["a"],
        winning="a",
        admitted=["a"],
        total_ms=8000,
        translation_ms=3500,
        translation_applied=True,
        contributed=True,
        language_bucket="banglish_codeswitch",
    )
    helpful_off = _paired_case(
        key="current_rebate_banglish",
        passed=True,
        recall=1.0,
        relevant_rank=3,
        relevant_ids=["a"],
        winning="a",
        admitted=["a"],
        total_ms=4000,
        language_bucket="banglish_codeswitch",
    )
    overhead_on = _paired_case(
        key="current_threshold",
        passed=True,
        recall=1.0,
        relevant_rank=1,
        relevant_ids=["t"],
        winning="t",
        admitted=["t"],
        total_ms=5000,
        translation_ms=2000,
        translation_applied=True,
        language_bucket="english",
    )
    overhead_off = _paired_case(
        key="current_threshold",
        passed=True,
        recall=1.0,
        relevant_rank=1,
        relevant_ids=["t"],
        winning="t",
        admitted=["t"],
        total_ms=3000,
        language_bucket="english",
    )
    harmful_on = _paired_case(
        key="stale_rebate_correction",
        passed=False,
        recall=0.5,
        relevant_rank=2,
        relevant_ids=["old"],
        winning="noise",
        admitted=["noise"],
        total_ms=7000,
        translation_ms=2500,
        translation_applied=True,
        language_bucket="english",
    )
    harmful_off = _paired_case(
        key="stale_rebate_correction",
        passed=True,
        recall=1.0,
        relevant_rank=1,
        relevant_ids=["new"],
        winning="new",
        admitted=["new"],
        total_ms=3200,
        language_bucket="english",
    )

    assert classify_translation_verdict(required_on, required_off) == "required"
    assert classify_translation_verdict(helpful_on, helpful_off) == "helpful"
    assert classify_translation_verdict(overhead_on, overhead_off) == "no_material_benefit"
    assert classify_translation_verdict(harmful_on, harmful_off) == "harmful"
    introduced = translation_changed_retrieval_outcome(required_on, required_off)
    assert introduced["introduced_relevant_candidate"] is True
    assert introduced["summary"] == "introduced_relevant"
    improved = translation_changed_retrieval_outcome(helpful_on, helpful_off)
    assert improved["improved_relevant_rank"] is True
    assert improved["summary"] == "improved_rank"
    unchanged = translation_changed_retrieval_outcome(overhead_on, overhead_off)
    assert unchanged["meaningful"] is False

    report = build_translation_comparison(
        {
            "name": "translation_on",
            "cases": [required_on, helpful_on, overhead_on, harmful_on],
            "effective_config": {
                "configuration": {"retrieval": {"query_translation_enabled": True}}
            },
        },
        {
            "name": "translation_off",
            "cases": [required_off, helpful_off, overhead_off, harmful_off],
            "effective_config": {
                "configuration": {"retrieval": {"query_translation_enabled": False}}
            },
        },
    )
    assert report["summary"]["required_cases"] == ["current_rebate_bangla"]
    assert report["summary"]["helpful_cases"] == ["current_rebate_banglish"]
    assert report["summary"]["pure_overhead_cases"] == ["current_threshold"]
    assert report["summary"]["harmful_cases"] == ["stale_rebate_correction"]
    assert report["latency"]["all"]["translation_share"]["overall"] > 0
    markdown = render_summary(
        {
            "journey": "tax_v1",
            "status": "failed",
            "run_id": "test",
            "project_id": "p",
            "job_transport": {"configured": "inline"},
            "cleanup": {"status": "succeeded"},
            "variants": [],
            "translation_comparison": report,
        }
    )
    assert "| Case | Lang | ON | OFF |" in markdown
    assert "`current_rebate_bangla`" in markdown
    assert "required" in markdown
    assert "Pure overhead" in markdown


def test_evaluate_case_result_records_translation_quality_without_changing_pass() -> None:
    chunk_id = uuid.uuid4()
    document_id = uuid.uuid4()
    case = JourneyCase(
        key="current_rebate_bangla",
        tags=["multilingual", "authority"],
        query="বর্তমান নিয়মে রিবেট কত?",
        anchors=["rebate"],
        expected_tokens=["10%"],
    )
    message = _message(
        content="The current rebate is 10%.",
        metadata={
            "retrieval_trace": {
                "retrieval_selected": [
                    {
                        "rank": 1,
                        "chunk_id": str(chunk_id),
                        "document_id": str(document_id),
                        "translated_dense": {"rank": 1, "rrf": 0.02},
                    }
                ],
                "context_selected": [{"chunk_id": str(chunk_id), "document_id": str(document_id)}],
                "translation": {"status": "applied", "latency_ms": 1200},
                "rerank": {"status": "applied", "latency_ms": 80},
                "executed_branches": ["original_dense", "translated_dense"],
            },
            "evidence_gate": {"sufficient": True},
            "web_search": {"status": "not_requested", "fallback_used": False},
        },
    )
    message.citations = [
        SimpleNamespace(
            source_kind=CitationSourceKind.KNOWLEDGE,
            chunk_id=chunk_id,
            document_id=document_id,
            filename="finance.md",
            source_revision_id=None,
            web_url=None,
        )
    ]

    result = evaluate_case_result(
        case=case,
        message=message,
        anchor_mapping={"rebate": [chunk_id]},
        document_ids={"finance_2026": document_id},
        response_mode=ResponseMode.INDEXED_ONLY,
        modifies_expansion_enabled=False,
    )

    assert result["passed"] is True
    assert result["language_bucket"] == "bangla"
    assert result["quality"]["translation_applied"] is True
    assert result["quality"]["translation_contributed_to_winning"] is True
    assert result["timings_ms"]["translation"] == 1200
    assert result["timings_ms"]["rerank"] == 80
    assert result["timings_ms"]["translation_share"] == 1200 / 5


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
