"""Small production-path RAG journey runner for the synthetic ``tax_v1`` fixture."""

from __future__ import annotations

import json
import math
import re
import statistics
import unicodedata
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import ResponseMode, Settings, StorageBackend
from app.modules.evaluation.metrics import rank_metrics
from app.modules.evaluation.schemas.evaluation import EvaluationCase, EvaluationCaseKind
from app.platform.config.project_ai import ProjectAIConfig
from app.platform.domain.text_tokenization import tokenize

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "journeys" / "tax_v1" / "journey.json"
DEFAULT_ARTIFACT_ROOT = _REPO_ROOT / "artifacts" / "rag-journey"
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_SECRET_KEYS = ("api_key", "password", "secret", "credential", "authorization")

# This is deliberately an explicit allowlist rather than every leaf currently
# exposed by ProjectAIConfig. New policy leaves default to rejected until they
# are classified as safe to compare against an existing corpus index.
RUNTIME_COMPARISON_CONFIG_KEYS = frozenset(
    {
        "llm.provider",
        "llm.model",
        "llm.temperature",
        "llm.max_tokens",
        "retrieval.strategy",
        "retrieval.top_k",
        "retrieval.rerank_enabled",
        "retrieval.rerank_mode",
        "retrieval.rerank_top_n",
        "retrieval.rerank_score_threshold",
        "retrieval.evidence_score_threshold",
        "retrieval.passage_scoring_enabled",
        "retrieval.passage_window_tokens",
        "retrieval.passage_overlap_tokens",
        "retrieval.passage_min_tokens",
        "retrieval.rerank_candidate_window",
        "retrieval.rerank_return_n",
        "retrieval.query_translation_enabled",
        "retrieval.modifies_expansion_enabled",
        "retrieval.modifies_expansion_mode",
        "retrieval.max_related_sources",
        "retrieval.max_relationship_candidates",
        "chat.response_mode",
        "chat.max_context_chunks",
        "chat.context_char_budget",
        "chat.max_history_messages",
        "chat.include_citations",
        "chat.citation_excerpt_max_chars",
        "chat.evidence_score_mode",
        "chat.evidence_gate_mode",
        "chat.lexical_corroboration_floor_score",
        "chat.minimum_query_token_coverage",
        "chat.minimum_claim_token_coverage",
        "chat.minimum_reranker_evidence_score",
        "chat.candidate_wise_grounding_enabled",
        "web_search.enabled",
        "web_search.model",
        "web_search.max_results",
        "web_search.max_evidence_chars",
        "web_search.max_output_tokens",
        "web_search.request_timeout_seconds",
        "domain_instructions",
        "prompt_profile",
        "prompt_version",
        "source_policy_mode",
    }
)


class JourneyError(RuntimeError):
    """Operator-facing journey setup or assertion failure."""


class JourneySource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    filename: str
    title: str
    revision_label: str
    source_type: str
    published_date: date
    effective_from: date
    effective_to: date | None = None
    modifies: list[str] = Field(default_factory=list)
    modified_provisions: dict[str, list[str]] = Field(default_factory=dict)


class EvidenceAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    source: str
    section: str
    phrases: list[str] = Field(min_length=1)
    expected_cardinality: int = Field(default=1, ge=1)


class CorrectionExpectation(BaseModel):
    """Minimal deterministic proof that a stale claim was actually corrected."""

    model_config = ConfigDict(extra="forbid")

    old_tokens: list[str] = Field(min_length=1)
    new_tokens: list[str] = Field(min_length=1)
    markers: list[str] = Field(min_length=1)


class JourneyCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    tags: list[str]
    query: str
    anchors: list[str]
    document_scope: str | None = None
    as_of: datetime | None = None
    expected_tokens: list[str] = Field(default_factory=list)
    expected_any: list[str] = Field(default_factory=list)
    correction: CorrectionExpectation | None = None
    mode: Literal["answerable", "scope_isolation", "no_answer"] = "answerable"


class JourneyManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int
    key: str
    description: str
    sources: list[JourneySource]
    anchors: list[EvidenceAnchor]
    cases: list[JourneyCase]


class RuntimeChunk(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    source_key: str
    chunk_index: int
    content: str
    metadata: dict[str, Any]


class JourneyOptions(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    fixture: Path = DEFAULT_FIXTURE
    artifact_root: Path = DEFAULT_ARTIFACT_ROOT
    overrides: dict[str, Any] = Field(default_factory=dict)
    comparison: tuple[str, Any] | None = None
    keep_project: bool = False
    allow_nonlocal_database: bool = False
    allow_nonlocal_storage: bool = False
    configured_job_backend: str | None = None


def load_manifest(path: Path = DEFAULT_FIXTURE) -> JourneyManifest:
    """Load and validate one journey pack without adding a generic fixture DSL."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JourneyError(f"Unable to load journey manifest {path}: {exc}") from exc
    manifest = JourneyManifest.model_validate(payload)
    source_keys = [source.key for source in manifest.sources]
    anchor_keys = [anchor.key for anchor in manifest.anchors]
    case_keys = [case.key for case in manifest.cases]
    for label, keys in (("source", source_keys), ("anchor", anchor_keys), ("case", case_keys)):
        if len(keys) != len(set(keys)):
            raise JourneyError(f"Journey manifest has duplicate {label} keys.")
    sources = set(source_keys)
    anchors = set(anchor_keys)
    for source in manifest.sources:
        unknown = set(source.modifies) - sources
        if unknown:
            raise JourneyError(f"Source {source.key!r} modifies unknown sources: {sorted(unknown)}")
        unknown_scopes = set(source.modified_provisions) - set(source.modifies)
        if unknown_scopes:
            raise JourneyError(
                f"Source {source.key!r} scopes non-MODIFIES targets: {sorted(unknown_scopes)}"
            )
    for anchor in manifest.anchors:
        if anchor.source not in sources:
            raise JourneyError(
                f"Anchor {anchor.key!r} references unknown source {anchor.source!r}."
            )
    for case in manifest.cases:
        unknown = set(case.anchors) - anchors
        if unknown:
            raise JourneyError(f"Case {case.key!r} references unknown anchors: {sorted(unknown)}")
        if case.document_scope is not None and case.document_scope not in sources:
            raise JourneyError(
                f"Case {case.key!r} references unknown document scope {case.document_scope!r}."
            )
    return manifest


def normalize_text(value: str) -> str:
    """Normalize prose and Unicode digits for deterministic phrase/number checks."""
    characters: list[str] = []
    for char in unicodedata.normalize("NFKC", value).casefold():
        try:
            characters.append(str(unicodedata.digit(char)))
        except (TypeError, ValueError):
            characters.append(char)
    normalized = "".join(characters).replace(",", "")
    return " ".join(re.sub(r"[^\w%]+", " ", normalized, flags=re.UNICODE).split())


def _scope_current_authority_response_is_safe(normalized_answer: str) -> bool:
    """Accept a scope-qualified refusal, not a scoped value labelled current."""
    tokens = set(normalized_answer.split())
    has_scope = bool(tokens & {"scope", "scoped", "document"})
    has_current = "current" in tokens
    has_unavailability = bool(tokens & {"cannot", "unavailable", "insufficient", "enough"})
    return has_scope and has_current and has_unavailability


def _contains_semantic_marker(normalized_answer: str, marker: str) -> bool:
    """Match short discourse markers across harmless inflection/wording changes."""
    normalized_marker = normalize_text(marker)
    if normalized_marker in normalized_answer:
        return True
    answer_forms = set().union(*(_token_forms(token) for token in normalized_answer.split()))
    marker_tokens = normalized_marker.split()
    return bool(marker_tokens) and all(
        _token_forms(token) & answer_forms for token in marker_tokens
    )


def _token_forms(token: str) -> set[str]:
    forms = {token}
    if len(token) > 4 and token.endswith("ies"):
        forms.add(f"{token[:-3]}y")
    if len(token) > 4 and token.endswith("ing"):
        forms.add(token[:-3])
    if len(token) > 3 and token.endswith("ed"):
        forms.update({token[:-2], token[:-1]})
    if len(token) > 3 and token.endswith("s"):
        forms.add(token[:-1])
    return forms


def resolve_evidence_anchors(
    anchors: list[EvidenceAnchor],
    chunks: list[RuntimeChunk],
) -> dict[str, list[uuid.UUID]]:
    """Resolve source/section/phrase anchors to ephemeral runtime chunk identities."""
    mappings: dict[str, list[uuid.UUID]] = {}
    for anchor in anchors:
        normalized_section = normalize_text(anchor.section)
        normalized_phrases = [normalize_text(phrase) for phrase in anchor.phrases]
        matches: list[RuntimeChunk] = []
        for chunk in chunks:
            if chunk.source_key != anchor.source:
                continue
            normalized_content = normalize_text(chunk.content)
            section_title = normalize_text(str(chunk.metadata.get("section_title") or ""))
            section_matches = (
                normalized_section in section_title or normalized_section in normalized_content
            )
            if section_matches and all(
                phrase in normalized_content for phrase in normalized_phrases
            ):
                matches.append(chunk)
        if len(matches) != anchor.expected_cardinality:
            identities = [f"{match.source_key}:{match.chunk_index}:{match.id}" for match in matches]
            raise JourneyError(
                f"Anchor {anchor.key!r} expected {anchor.expected_cardinality} chunk(s), "
                f"found {len(matches)}: {identities}"
            )
        mappings[anchor.key] = [match.id for match in matches]
    return mappings


def _project_ai_leaf_paths() -> set[str]:
    paths: set[str] = set()

    def visit(model: type[BaseModel], prefix: str = "") -> None:
        for name, field in model.model_fields.items():
            path = f"{prefix}.{name}" if prefix else name
            annotation = field.annotation
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                visit(annotation, path)
            else:
                paths.add(path)

    visit(ProjectAIConfig)
    return paths


SAFE_CONFIG_KEYS = RUNTIME_COMPARISON_CONFIG_KEYS


def parse_config_assignment(raw: str) -> tuple[str, Any]:
    """Parse and validate a query-time ProjectAIConfig leaf assignment."""
    if "=" not in raw:
        raise JourneyError(f"Config assignment must be KEY=VALUE, received {raw!r}.")
    key, raw_value = (part.strip() for part in raw.split("=", 1))
    if key not in SAFE_CONFIG_KEYS:
        raise JourneyError(
            f"Unsafe or unknown journey config key {key!r}. Only runtime ProjectAIConfig "
            "leaves may be changed; index-affecting settings are not exposed."
        )
    if not raw_value:
        raise JourneyError(f"Config assignment {key!r} has an empty value.")
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        value = raw_value
    return key, value


def build_project_config(overrides: Mapping[str, Any]) -> ProjectAIConfig:
    """Build sparse Project configuration and let production validation own types/ranges."""
    payload: dict[str, Any] = {}
    for key, value in overrides.items():
        if key not in SAFE_CONFIG_KEYS:
            raise JourneyError(f"Unsafe or unknown journey config key {key!r}.")
        cursor = payload
        parts = key.split(".")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value
    try:
        return ProjectAIConfig.model_validate(payload)
    except ValueError as exc:
        raise JourneyError(f"Invalid Project AI config override: {exc}") from exc


def validate_safe_targets(
    settings: Settings,
    *,
    allow_nonlocal_database: bool,
    allow_nonlocal_storage: bool,
) -> None:
    """Fail closed before creating state on unexpected database/storage targets."""
    database_host = settings.database.host.casefold().strip("[]")
    if database_host not in _LOOPBACK_HOSTS and not allow_nonlocal_database:
        raise JourneyError(
            f"Refusing non-loopback database host {settings.database.host!r}; "
            "pass --allow-nonlocal-database to acknowledge it explicitly."
        )
    if settings.storage.backend is StorageBackend.LOCAL:
        root = Path(settings.storage.local_root).expanduser().resolve()
        if root == Path(root.anchor) or root == _REPO_ROOT.resolve():
            raise JourneyError(f"Unsafe local object-storage root: {root}")
        return
    if settings.storage.backend is StorageBackend.MINIO:
        endpoint = settings.minio.endpoint
        host = urlsplit(endpoint if "://" in endpoint else f"//{endpoint}").hostname
        if (host or "").casefold() not in _LOOPBACK_HOSTS and not allow_nonlocal_storage:
            raise JourneyError(
                f"Refusing non-loopback MinIO endpoint {endpoint!r}; "
                "pass --allow-nonlocal-storage to acknowledge it explicitly."
            )


def sanitize_diagnostics(value: Any) -> Any:
    """Retain explainability while recursively removing credential-shaped fields."""
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).casefold()
            output[str(key)] = (
                "[redacted]"
                if any(secret in lowered for secret in _SECRET_KEYS)
                else sanitize_diagnostics(item)
            )
        return output
    if isinstance(value, (list, tuple)):
        return [sanitize_diagnostics(item) for item in value]
    if isinstance(value, (datetime, date, uuid.UUID, Path)):
        return str(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _trace_identities(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    keys = {
        "rank",
        "source_kind",
        "chunk_id",
        "document_id",
        "chunk_index",
        "retrieval_source",
        "score",
        "semantic_score",
        "rank_score",
        "rerank_relevance_score",
        "branch_provenance",
        "evidence_unit_id",
        "authority_redaction",
        "authority_redacted_provisions",
        "web_url",
        "web_title",
        "web_provider",
    }
    return [
        {key: item.get(key) for key in keys if key in item}
        for item in items
        if isinstance(item, dict)
    ]


def _failure(stage: str, message: str) -> dict[str, str]:
    return {"stage": stage, "message": message}


def _knowledge_document_ids(items: list[dict[str, Any]]) -> set[str]:
    return {
        str(item["document_id"])
        for item in items
        if item.get("document_id") and item.get("source_kind", "knowledge") == "knowledge"
    }


def evaluate_case_result(
    *,
    case: JourneyCase,
    message: Any,
    anchor_mapping: Mapping[str, list[uuid.UUID]],
    document_ids: Mapping[str, uuid.UUID],
    response_mode: ResponseMode,
    modifies_expansion_enabled: bool,
) -> dict[str, Any]:
    """Normalize one production message and localize deterministic failures."""
    metadata = dict(message.metadata or {})
    trace = dict(metadata.get("retrieval_trace") or {})
    candidates = _trace_identities(trace.get("candidates"))
    retrieved = _trace_identities(trace.get("retrieval_selected"))
    admitted = _trace_identities(trace.get("context_selected"))
    citations = [
        {
            "source_kind": citation.source_kind.value,
            "chunk_id": str(citation.chunk_id) if citation.chunk_id else None,
            "document_id": str(citation.document_id) if citation.document_id else None,
            "filename": citation.filename,
            "source_revision_id": (
                str(citation.source_revision_id) if citation.source_revision_id else None
            ),
            "web_url": citation.web_url,
        }
        for citation in message.citations
    ]
    claims = [
        {
            "claim_id": claim.claim_id,
            "text": claim.text,
            "grounded": claim.grounded,
            "verification": claim.verification.value,
            "evidence": [
                {
                    "source_kind": evidence.source_kind.value,
                    "chunk_id": str(evidence.chunk_id) if evidence.chunk_id else None,
                    "document_id": str(evidence.document_id) if evidence.document_id else None,
                }
                for evidence in claim.evidence
            ],
        }
        for claim in message.claims
    ]
    relevant_ids = {
        str(chunk_id) for anchor in case.anchors for chunk_id in anchor_mapping.get(anchor, [])
    }
    result_ids = [str(item.get("chunk_id")) for item in retrieved if item.get("chunk_id")]
    recall, reciprocal_rank, ndcg, relevant_retrieved = rank_metrics(result_ids, relevant_ids)
    admitted_ids = {str(item.get("chunk_id")) for item in admitted if item.get("chunk_id")}
    citation_ids = {
        str(item["chunk_id"])
        for item in citations
        if item["source_kind"] == "knowledge" and item.get("chunk_id")
    }
    normalized_answer = normalize_text(message.content)
    web = dict(metadata.get("web_search") or {})
    gate = dict(metadata.get("evidence_gate") or {})
    authority = dict(metadata.get("current_authority") or {})
    scope_current_authority = dict(metadata.get("scope_current_authority") or {})
    failures: list[dict[str, str]] = []
    all_knowledge_evidence = candidates + retrieved + admitted + citations
    finalized_knowledge_evidence = admitted + citations
    for claim in claims:
        all_knowledge_evidence.extend(claim["evidence"])
        finalized_knowledge_evidence.extend(claim["evidence"])

    if case.document_scope is not None:
        expected_document = str(document_ids[case.document_scope])
        leaked = _knowledge_document_ids(all_knowledge_evidence) - {expected_document}
        if leaked:
            failures.append(
                _failure("authority", f"Hard document scope leaked evidence from {sorted(leaked)}.")
            )
    if "historical" in case.tags:
        historical_document = str(document_ids["tax_2023"])
        leaked = _knowledge_document_ids(finalized_knowledge_evidence) - {historical_document}
        if leaked:
            failures.append(
                _failure("authority", f"Historical answer used future evidence: {sorted(leaked)}.")
            )

    if case.mode == "answerable":
        if not relevant_retrieved:
            failures.append(_failure("retrieval", "No expected evidence anchor was retrieved."))
        if relevant_ids and not (admitted_ids & relevant_ids):
            failures.append(
                _failure("admission_grounding", "Expected retrieved evidence was not admitted.")
            )
        for token in case.expected_tokens:
            if normalize_text(token) not in normalized_answer:
                failures.append(
                    _failure("generation_refusal", f"Answer is missing expected fact {token!r}.")
                )
        if case.expected_any and not any(
            _contains_semantic_marker(normalized_answer, token) for token in case.expected_any
        ):
            failures.append(
                _failure(
                    "generation_refusal",
                    f"Answer contains none of the expected semantic markers {case.expected_any!r}.",
                )
            )
        if case.correction is not None:
            missing_new = [
                token
                for token in case.correction.new_tokens
                if normalize_text(token) not in normalized_answer
            ]
            has_correction_marker = any(
                _contains_semantic_marker(normalized_answer, marker)
                for marker in case.correction.markers
            )
            if missing_new or not has_correction_marker:
                failures.append(
                    _failure(
                        "generation_refusal",
                        "Answer did not explicitly correct the stale claim "
                        f"(stale_facts_need_not_be_repeated={case.correction.old_tokens}, "
                        f"missing_new={missing_new}, "
                        f"correction_marker={has_correction_marker}).",
                    )
                )
        if message.insufficient_evidence_reason is not None or not message.grounded:
            failures.append(_failure("admission_grounding", "Answerable case was not grounded."))
        if not bool(gate.get("sufficient")):
            failures.append(
                _failure("admission_grounding", "Indexed evidence did not pass the grounding gate.")
            )
        if relevant_ids and not (citation_ids & relevant_ids):
            failures.append(_failure("citation", "No citation points at expected evidence."))
        if bool(web.get("fallback_used")):
            failures.append(_failure("fallback", "Indexed answer unnecessarily used web fallback."))
        if (
            response_mode in {ResponseMode.INDEXED_ONLY, ResponseMode.INDEXED_THEN_WEB}
            and str(web.get("status")) != "not_requested"
        ):
            failures.append(_failure("fallback", "Sufficient indexed answer requested web search."))

    if case.mode == "scope_isolation":
        if case.anchors and not relevant_retrieved:
            failures.append(
                _failure("retrieval", "No expected scoped evidence anchor was retrieved.")
            )
        if modifies_expansion_enabled and authority.get("status") != "suppressed_document_scope":
            failures.append(
                _failure(
                    "authority",
                    "MODIFIES expansion did not report suppressed_document_scope.",
                )
            )
        if bool(web.get("fallback_used")) or web.get("status") not in {
            "not_requested",
            "suppressed_scoped_request",
        }:
            failures.append(_failure("fallback", "Hard-scoped case did not suppress web fallback."))
        web_would_be_eligible = response_mode is ResponseMode.INDEXED_AND_WEB or (
            response_mode is ResponseMode.INDEXED_THEN_WEB and not bool(gate.get("sufficient"))
        )
        if web_would_be_eligible and web.get("status") != "suppressed_scoped_request":
            failures.append(
                _failure("fallback", "Eligible web fallback was not suppressed by hard scope.")
            )
        if "current" in set(tokenize(case.query)):
            if scope_current_authority.get("status") != "unavailable_within_hard_scope":
                failures.append(
                    _failure(
                        "authority",
                        "Current hard-scope request was not marked unavailable within scope.",
                    )
                )
            if not _scope_current_authority_response_is_safe(normalized_answer):
                failures.append(
                    _failure(
                        "generation_refusal",
                        "Hard-scoped current response did not distinguish scoped evidence "
                        "from current authority.",
                    )
                )

    if case.mode == "no_answer":
        indexed_admitted = [
            item for item in admitted if item.get("source_kind", "knowledge") == "knowledge"
        ]
        if indexed_admitted:
            failures.append(
                _failure("admission_grounding", "Unknown case admitted indexed evidence.")
            )
        if bool(gate.get("sufficient")):
            failures.append(
                _failure("admission_grounding", "Unknown case passed indexed grounding gate.")
            )
        if response_mode is ResponseMode.INDEXED_ONLY and (
            message.insufficient_evidence_reason is None
            or gate.get("generation_ran") is not False
            or web.get("status") != "not_requested"
        ):
            failures.append(
                _failure(
                    "generation_refusal",
                    "Indexed-only unknown case did not refuse before generation/web fallback.",
                )
            )

    return {
        "key": case.key,
        "tags": case.tags,
        "mode": case.mode,
        "passed": not failures,
        "failures": failures,
        "answer": message.content,
        "grounded": message.grounded,
        "insufficient_evidence_reason": (
            message.insufficient_evidence_reason.value
            if message.insufficient_evidence_reason is not None
            else None
        ),
        "source_provenance": message.source_provenance.value,
        "retrieval": {
            "candidates": candidates,
            "selected": retrieved,
            "recall": recall,
            "reciprocal_rank": reciprocal_rank,
            "ndcg": ndcg,
        },
        "admitted": admitted,
        "citations": citations,
        "claims": claims,
        "authority": sanitize_diagnostics(authority),
        "evidence_gate": sanitize_diagnostics(gate),
        "fallback": sanitize_diagnostics(web),
        "translation": sanitize_diagnostics(trace.get("translation") or {}),
        "rerank": sanitize_diagnostics(trace.get("rerank") or {}),
        "timings_ms": {
            "retrieval": message.retrieval_latency_ms,
            "generation": message.provider_latency_ms,
            "total": message.total_latency_ms,
        },
    }


def aggregate_results(cases: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [
        float(case["timings_ms"]["total"])
        for case in cases
        if isinstance(case.get("timings_ms", {}).get("total"), (int, float))
    ]
    return {
        "case_count": len(cases),
        "passed": sum(bool(case["passed"]) for case in cases),
        "pass_rate": sum(bool(case["passed"]) for case in cases) / len(cases) if cases else 0.0,
        "mean_recall": statistics.fmean(
            float(case["retrieval"]["recall"]) for case in cases if case["mode"] == "answerable"
        )
        if any(case["mode"] == "answerable" for case in cases)
        else 0.0,
        "latency_p50_ms": statistics.median(latencies) if latencies else 0.0,
        "latency_p95_ms": _percentile(latencies, 0.95),
        "failure_counts": {
            stage: sum(
                failure["stage"] == stage for case in cases for failure in case.get("failures", [])
            )
            for stage in (
                "retrieval",
                "authority",
                "admission_grounding",
                "generation_refusal",
                "citation",
                "fallback",
            )
        },
    }


def tag_aggregates(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        tag: aggregate_results([case for case in cases if tag in case["tags"]])
        for tag in ("multilingual", "authority", "scope", "refusal")
    }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[position]


Progress = Callable[[str], None]


async def _file_stream(path: Path, *, chunk_size: int = 64 * 1024) -> AsyncIterator[bytes]:
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            yield chunk


def _active_record(revision: Any) -> Any:
    from app.platform.config.project_ai import ConfigRevisionRecord

    if revision is None:
        return None
    return ConfigRevisionRecord(
        id=revision.id,
        revision_number=revision.revision_number,
        configuration_hash=revision.configuration_hash,
        configuration=dict(revision.configuration),
    )


async def _activate_configuration(
    session: Any,
    *,
    project_id: uuid.UUID,
    settings: Settings,
    configuration: ProjectAIConfig,
    expected_revision_id: uuid.UUID | None,
    reason: str,
) -> Any:
    from app.composition.audit import DatabaseAuditRecorder
    from app.modules.projects.repositories.project_ai_config_repository import (
        ProjectAIConfigRepository,
    )
    from app.modules.projects.services.project_ai_config_service import (
        ProjectAdministrationService,
    )

    repository = ProjectAIConfigRepository(session, project_id)
    service = ProjectAdministrationService(
        session=session,
        project_id=project_id,
        repository=repository,
        settings=settings,
        audit=DatabaseAuditRecorder(session, project_id),
        actor_id="rag-journey",
    )
    return await service.create_revision(
        configuration,
        expected_active_revision_id=expected_revision_id,
        reason=reason,
    )


async def _document_service(
    session: Any,
    *,
    project_id: uuid.UUID,
    settings: Settings,
    storage: Any,
) -> Any:
    from app.composition.audit import DatabaseAuditRecorder
    from app.composition.jobs import build_job_service
    from app.dependencies.knowledge import get_document_service
    from app.modules.knowledge.repositories.document_repository import DocumentRepository
    from app.modules.knowledge.repositories.source_metadata_repository import (
        SourceMetadataRepository,
    )
    from app.modules.knowledge.services.source_metadata_service import SourceMetadataService
    from app.platform.jobs.implementations.inline_queue import InlineJobQueue

    source_metadata = SourceMetadataService(
        session,
        SourceMetadataRepository(session, project_id),
        audit=DatabaseAuditRecorder(session, project_id),
        actor_id="rag-journey",
    )
    jobs = build_job_service(
        session=session,
        project_id=project_id,
        settings=settings,
        queue=InlineJobQueue(),
    )
    return await get_document_service(
        session,
        DocumentRepository(session, project_id),
        storage,
        jobs,
        source_metadata,
    )


async def _preflight_default_organization(session: Any) -> None:
    from app.models.organization import Organization
    from app.platform.domain.auth_context import DEFAULT_ORGANIZATION_ID

    organization = await session.get(Organization, DEFAULT_ORGANIZATION_ID)
    if organization is None or organization.deleted_at is not None or not organization.is_active:
        raise JourneyError(
            "The configured default/local Organization "
            f"{DEFAULT_ORGANIZATION_ID} does not exist or is inactive. Create/activate that "
            "exact Organization before running rag-journey; no arbitrary Organization is used."
        )


async def _create_project(session: Any, *, run_token: str) -> Any:
    from app.modules.projects.repositories.project_repository import ProjectRepository
    from app.modules.projects.schemas.project import ProjectCreate
    from app.modules.projects.services.project_service import ProjectService

    service = ProjectService(
        session,
        ProjectRepository(session),
        actor_id="rag-journey",
    )
    return await service.create(
        ProjectCreate(
            name=f"RAG Journey tax_v1 {run_token}",
            description=f"Temporary local RAG journey project; run_token={run_token}",
        )
    )


async def _ingest_sources(
    session_factory: Any,
    *,
    manifest: JourneyManifest,
    fixture_root: Path,
    project_id: uuid.UUID,
    settings: Settings,
    storage: Any,
    progress: Progress,
    document_ids: dict[str, uuid.UUID],
) -> dict[str, uuid.UUID]:
    from app.models.source_metadata import (
        SourceLifecycleStatus,
        SourceRelationshipType,
        SourceRole,
    )
    from app.modules.knowledge.repositories.source_metadata_repository import (
        SourceMetadataRepository,
    )
    from app.modules.knowledge.schemas.document import DocumentIngestInput
    from app.modules.knowledge.schemas.source_metadata import (
        SourceRelationshipCreate,
        SourceRevisionCreate,
    )
    from app.modules.knowledge.services.source_metadata_service import SourceMetadataService

    revision_ids: dict[str, uuid.UUID] = {}
    for source in manifest.sources:
        relationships = [
            SourceRelationshipCreate(
                relationship_type=SourceRelationshipType.MODIFIES,
                target_revision_id=revision_ids[target],
                target_provisions=source.modified_provisions.get(target, []),
            )
            for target in source.modifies
        ]
        metadata = SourceRevisionCreate(
            create_new_group=True,
            revision_label=source.revision_label,
            title=source.title,
            source_type=source.source_type,
            published_date=source.published_date,
            effective_from=source.effective_from,
            effective_to=source.effective_to,
            lifecycle_status=SourceLifecycleStatus.ACTIVE,
            source_role=SourceRole.PRIMARY,
            relationships=relationships,
            change_reason=f"tax_v1 fixture source {source.key}",
            activate=True,
        )
        path = fixture_root / "corpus" / source.filename
        if not path.is_file():
            raise JourneyError(f"Journey source file does not exist: {path}")
        progress(f"ingest {source.key}: {source.filename}")
        async with session_factory() as session:
            service = await _document_service(
                session,
                project_id=project_id,
                settings=settings,
                storage=storage,
            )
            document = await service.upload(
                DocumentIngestInput(
                    filename=source.filename,
                    content_type="text/markdown",
                    stream=_file_stream(path),
                    source_metadata=metadata,
                )
            )
            document_ids[source.key] = document.id
        async with session_factory() as session:
            source_service = SourceMetadataService(
                session,
                SourceMetadataRepository(session, project_id),
                actor_id="rag-journey",
            )
            active = await source_service.active_for_document(document_ids[source.key])
            revision_ids[source.key] = active.revision.id
    return revision_ids


async def _ensure_indexed(
    session_factory: Any,
    *,
    project_id: uuid.UUID,
    document_ids: Mapping[str, uuid.UUID],
    settings: Settings,
) -> dict[str, Any]:
    from app.composition.retrieval import build_indexing_service
    from app.models.document import DocumentStatus
    from app.modules.knowledge.repositories.document_repository import DocumentRepository
    from app.modules.retrieval.repositories.index_build_repository import IndexBuildRepository
    from app.platform.jobs.implementations.inline_queue import InlineJobQueue

    indexable = {
        DocumentStatus.CHUNKED,
        DocumentStatus.EMBEDDED,
        DocumentStatus.READY,
    }

    async def active_build() -> Any:
        async with session_factory() as session:
            return await IndexBuildRepository(session, project_id).get_active()

    async def index_document(source_key: str, document_id: uuid.UUID) -> None:
        async with session_factory() as session:
            document = await DocumentRepository(session, project_id).get_by_id(
                document_id, include_deleted=True
            )
            if document is None:
                raise JourneyError(f"Indexing target {source_key!r} disappeared during setup.")
            if document.status not in indexable:
                raise JourneyError(
                    f"Document {source_key!r} has status {document.status.value}; expected "
                    "chunked, embedded, or ready. The required production embed/index path "
                    "cannot run for this indexing mode."
                )
            if document.status in {DocumentStatus.CHUNKED, DocumentStatus.READY}:
                indexing = build_indexing_service(
                    session=session,
                    project_id=project_id,
                    settings=settings,
                    job_queue=InlineJobQueue(),
                )
                await indexing.enqueue_embed(document.id)
        async with session_factory() as session:
            document = await DocumentRepository(session, project_id).get_by_id(
                document_id, include_deleted=True
            )
            if document is None:
                raise JourneyError(f"Indexing target {source_key!r} disappeared during setup.")
            if document.status is DocumentStatus.EMBEDDED:
                indexing = build_indexing_service(
                    session=session,
                    project_id=project_id,
                    settings=settings,
                    job_queue=InlineJobQueue(),
                )
                await indexing.enqueue_index(document.id)
            elif document.status is not DocumentStatus.READY:
                raise JourneyError(
                    f"Document {source_key!r} remained {document.status.value} after the "
                    "production embed/index path."
                )

    build = await active_build()
    if build is None or build.document_count != len(document_ids):
        for source_key, document_id in document_ids.items():
            await index_document(source_key, document_id)
        build = await active_build()
    if build is None:
        raise JourneyError("No validated active index build exists after ingestion.")
    if build.document_count != len(document_ids):
        raise JourneyError(
            f"Active index contains {build.document_count} documents; expected {len(document_ids)}."
        )
    if (
        build.chunk_count <= 0
        or build.vector_count != build.chunk_count
        or build.keyword_count <= 0
    ):
        raise JourneyError(
            "Active index failed corpus verification: "
            f"chunks={build.chunk_count}, vectors={build.vector_count}, "
            f"keywords={build.keyword_count}."
        )
    return {
        "id": str(build.id),
        "state": build.state.value,
        "document_count": build.document_count,
        "chunk_count": build.chunk_count,
        "vector_count": build.vector_count,
        "keyword_count": build.keyword_count,
        "embedding_set_version": build.embedding_set_version,
        "configuration_hash": build.configuration_hash,
        "corpus_fingerprint": build.corpus_fingerprint,
    }


async def _runtime_chunks(
    session_factory: Any,
    *,
    project_id: uuid.UUID,
    document_ids: Mapping[str, uuid.UUID],
) -> list[RuntimeChunk]:
    from app.modules.knowledge.repositories.document_chunk_repository import (
        DocumentChunkRepository,
    )

    chunks: list[RuntimeChunk] = []
    async with session_factory() as session:
        repository = DocumentChunkRepository(session, project_id)
        for source_key, document_id in document_ids.items():
            rows = await repository.list_by_document(document_id, limit=10_000, offset=0)
            if not rows:
                raise JourneyError(f"Document {source_key!r} produced no chunks.")
            chunks.extend(
                RuntimeChunk(
                    id=row.id,
                    document_id=row.document_id,
                    source_key=source_key,
                    chunk_index=row.chunk_index,
                    content=row.content,
                    metadata=dict(row.chunk_metadata),
                )
                for row in rows
            )
    return chunks


def _evaluation_case(
    case: JourneyCase,
    *,
    anchor_mapping: Mapping[str, list[uuid.UUID]],
    document_ids: Mapping[str, uuid.UUID],
) -> EvaluationCase:
    if case.mode == "no_answer":
        kind = EvaluationCaseKind.NO_ANSWER
    elif "multilingual" in case.tags:
        kind = EvaluationCaseKind.CROSS_LINGUAL
    elif case.document_scope:
        kind = EvaluationCaseKind.METADATA_FILTER
    else:
        kind = EvaluationCaseKind.CITATION
    return EvaluationCase(
        key=case.key,
        kind=kind,
        query=case.query,
        relevant_chunk_ids=[
            chunk_id for anchor in case.anchors for chunk_id in anchor_mapping[anchor]
        ],
        document_id=document_ids.get(case.document_scope or ""),
        as_of=case.as_of,
        expected_answer_tokens=case.expected_tokens,
        expected_no_answer=case.mode == "no_answer",
        query_language=("bn" if case.key.endswith("bangla") else None),
        expected_evidence_language=("en" if "multilingual" in case.tags else None),
        query_form=("banglish" if case.key.endswith("banglish") else None),
    )


async def _run_variant(
    session_factory: Any,
    *,
    name: str,
    manifest: JourneyManifest,
    project_id: uuid.UUID,
    document_ids: Mapping[str, uuid.UUID],
    anchor_mapping: Mapping[str, list[uuid.UUID]],
    settings: Settings,
    resolution: Any,
    progress: Progress,
) -> dict[str, Any]:
    from app.composition.audit import DatabaseAuditRecorder
    from app.dependencies.conversations import get_chat_service
    from app.modules.conversations.repositories.conversation_repository import (
        ConversationRepository,
    )
    from app.modules.conversations.repositories.message_repository import MessageRepository
    from app.modules.conversations.schemas.conversation import ConversationCreate
    from app.modules.conversations.schemas.message import MessageSendRequest
    from app.modules.conversations.services.conversation_service import ConversationService
    from app.modules.projects.repositories.project_ai_config_repository import (
        ProjectAIConfigRepository,
    )
    from app.platform.providers.implementations.embedding_factory import (
        create_embedding_provider,
    )

    effective = resolution.configuration
    variant_results: list[dict[str, Any]] = []
    embedder = create_embedding_provider(settings)
    for case in manifest.cases:
        expected = _evaluation_case(
            case,
            anchor_mapping=anchor_mapping,
            document_ids=document_ids,
        )
        progress(f"{name}: {case.key}")
        async with session_factory() as session:
            conversations = ConversationRepository(session, project_id)
            messages = MessageRepository(session, project_id)
            revision = await ProjectAIConfigRepository(session, project_id).get_active()
            service = ConversationService(
                session=session,
                project_id=project_id,
                conversation_repository=conversations,
                message_repository=messages,
                llm_config=settings.llm,
                chat_config=settings.chat,
                settings=settings,
                active_revision=_active_record(revision),
                actor_id="rag-journey",
                audit=DatabaseAuditRecorder(session, project_id),
            )
            conversation = await service.create(ConversationCreate(title=f"{name}: {case.key}"))
            chat = await get_chat_service(
                session,
                project_id,
                conversations,
                messages,
                conversation.id,
                embedder,
            )
            turn = await chat.send_message(
                conversation.id,
                MessageSendRequest(
                    content=case.query,
                    document_id=expected.document_id,
                    as_of=case.as_of,
                ),
            )
            normalized = evaluate_case_result(
                case=case,
                message=turn.assistant_message,
                anchor_mapping=anchor_mapping,
                document_ids=document_ids,
                response_mode=effective.chat.response_mode,
                modifies_expansion_enabled=(
                    effective.retrieval.modifies_expansion_enabled
                    or effective.retrieval.modifies_expansion_mode.value == "expand"
                ),
            )
            normalized["conversation_id"] = str(conversation.id)
            normalized["expected"] = sanitize_diagnostics(expected.model_dump(mode="json"))
            variant_results.append(normalized)
            stages = ",".join(failure["stage"] for failure in normalized["failures"])
            progress(
                f"{name}: {case.key} {'PASS' if normalized['passed'] else f'FAIL[{stages}]'} "
                f"({normalized['timings_ms']['total'] or 0} ms)"
            )
    return {
        "name": name,
        "effective_config": resolution.secret_free_snapshot(),
        "providers": {
            "llm": {
                "provider": effective.llm.provider.value,
                "model": effective.llm.model,
            },
            "embedding": {
                "provider": settings.embedding.backend.value,
                "model": settings.embedding.model,
                "dimensions": settings.embedding.dimensions,
            },
            "reranker": {
                "provider": (
                    effective.retrieval.reranker_backend.value
                    if effective.retrieval.reranker_backend
                    else None
                ),
                "model": effective.retrieval.reranker_model,
            },
            "translation": {
                "provider": effective.retrieval.query_translation_backend,
                "model": effective.retrieval.query_translation_model,
            },
        },
        "cases": variant_results,
        "aggregate": aggregate_results(variant_results),
        "tag_aggregates": tag_aggregates(variant_results),
    }


async def _effective_resolution(session: Any, *, project_id: uuid.UUID, settings: Settings) -> Any:
    from app.modules.projects.repositories.project_ai_config_repository import (
        ProjectAIConfigRepository,
    )
    from app.platform.config.project_ai import resolve_project_ai_config

    revision = await ProjectAIConfigRepository(session, project_id).get_active()
    return resolve_project_ai_config(
        settings, _active_record(revision), validate_web_provider=False
    )


def source_purge_order(sources: list[JourneySource]) -> list[str]:
    """Return modifiers before the sources they reference.

    Source relationships are stored on the modifying revision, so deleting a
    target before its modifier violates the relationship foreign key.  The
    fixture graph is the authoritative lifecycle contract for this tiny tool;
    do not rely on source or document insertion order.
    """
    by_key = {source.key: source for source in sources}
    source_position = {source.key: index for index, source in enumerate(sources)}
    incoming_edges = {source.key: 0 for source in sources}
    for source in sources:
        for target_key in source.modifies:
            incoming_edges[target_key] += 1
    order: list[str] = []
    ready = sorted(
        (source_key for source_key, count in incoming_edges.items() if count == 0),
        key=source_position.__getitem__,
    )
    while ready:
        source_key = ready.pop(0)
        order.append(source_key)
        for target_key in by_key[source_key].modifies:
            incoming_edges[target_key] -= 1
            if incoming_edges[target_key] == 0:
                ready.append(target_key)
        ready.sort(key=source_position.__getitem__)
    if len(order) != len(sources):
        cyclic = sorted(source_key for source_key, count in incoming_edges.items() if count > 0)
        raise JourneyError(f"Source MODIFIES graph contains a cycle: {cyclic}")
    return order


async def _cleanup_project(
    session_factory: Any,
    *,
    project_id: uuid.UUID,
    run_token: str,
    document_ids: Mapping[str, uuid.UUID],
    sources: list[JourneySource],
    settings: Settings,
    storage: Any,
    progress: Progress,
) -> None:
    """Purge documents through production lifecycle, then remove the exact aggregate."""
    from sqlalchemy import select

    from app.models.document import Document
    from app.models.index_build import ProjectIndexPointer
    from app.models.project import Project

    purge_targets = {
        source_key: document_ids[source_key]
        for source_key in source_purge_order(sources)
        if source_key in document_ids
    }
    known_document_ids = set(purge_targets.values())
    async with session_factory() as session:
        rows = await session.execute(
            select(Document.id, Document.filename).where(Document.project_id == project_id)
        )
        for document_id, filename in rows.all():
            if document_id not in known_document_ids:
                purge_targets[str(filename)] = document_id
                known_document_ids.add(document_id)
    for source_key, document_id in purge_targets.items():
        progress(f"cleanup: purge {source_key}")
        async with session_factory() as session:
            project = await session.get(Project, project_id)
            if project is None or run_token not in (project.description or ""):
                raise JourneyError("Cleanup guard rejected an unexpected Project aggregate.")
            service = await _document_service(
                session,
                project_id=project_id,
                settings=settings,
                storage=storage,
            )
            await service.purge(document_id)
    remaining_keys = await storage.list_keys(f"{project_id}/")
    if remaining_keys:
        raise JourneyError(
            f"Project storage prefix is not empty after document purge: {remaining_keys[:10]}"
        )
    progress("cleanup: delete temporary Project aggregate")
    async with session_factory() as session:
        project = await session.get(Project, project_id, with_for_update=True)
        if project is None:
            raise JourneyError("Cleanup guard could not find the temporary Project.")
        if project.id != project_id or run_token not in (project.description or ""):
            raise JourneyError("Cleanup guard rejected an unexpected Project aggregate.")
        pointer = await session.get(ProjectIndexPointer, project_id, with_for_update=True)
        if pointer is not None:
            pointer.active_build_id = None
            pointer.previous_build_id = None
        project.active_ai_config_revision_id = None
        await session.flush()
        await session.delete(project)
        await session.commit()
    async with session_factory() as session:
        if await session.get(Project, project_id) is not None:
            raise JourneyError("Temporary Project still exists after aggregate deletion.")


def _comparison_summary(
    baseline: dict[str, Any],
    comparison: dict[str, Any],
    *,
    key: str,
) -> dict[str, Any]:
    affected: list[str] = []
    if key.startswith("retrieval.query_translation"):
        affected.append("multilingual")
    if key.startswith("retrieval.modifies") or key == "source_policy_mode":
        affected.extend(["authority", "scope"])
    if key.startswith(("chat.", "web_search.")):
        affected.append("refusal")
    affected = list(dict.fromkeys(affected))

    def delta(left: Mapping[str, Any], right: Mapping[str, Any], metric: str) -> float:
        return float(right.get(metric, 0.0)) - float(left.get(metric, 0.0))

    tags: dict[str, Any] = {}
    for tag in ("multilingual", "authority", "scope", "refusal"):
        left = baseline["tag_aggregates"][tag]
        right = comparison["tag_aggregates"][tag]
        tags[tag] = {
            "affected": tag in affected,
            "case_count": right["case_count"],
            "pass_rate_delta": delta(left, right, "pass_rate"),
            "mean_recall_delta": delta(left, right, "mean_recall"),
            "latency_p50_ms_delta": delta(left, right, "latency_p50_ms"),
        }
    return {
        "key": key,
        "affected_tags": affected,
        "overall": {
            "pass_rate_delta": delta(baseline["aggregate"], comparison["aggregate"], "pass_rate"),
            "mean_recall_delta": delta(
                baseline["aggregate"], comparison["aggregate"], "mean_recall"
            ),
            "latency_p50_ms_delta": delta(
                baseline["aggregate"], comparison["aggregate"], "latency_p50_ms"
            ),
        },
        "tags": tags,
    }


def render_summary(result: Mapping[str, Any]) -> str:
    lines = [
        f"# RAG Journey: {result['journey']}",
        "",
        f"- Status: **{str(result['status']).upper()}**",
        f"- Run ID: `{result['run_id']}`",
        f"- Project ID: `{result.get('project_id') or 'not-created'}`",
        f"- Job transport: inline (configured: {result['job_transport']['configured']})",
        f"- Cleanup: {result['cleanup']['status']}",
        "",
    ]
    if result.get("setup_error"):
        lines.extend(["## Setup failure", "", str(result["setup_error"]), ""])
    if result.get("index"):
        index = result["index"]
        lines.extend(
            [
                "## Corpus",
                "",
                f"Active build `{index['id']}`: {index['document_count']} documents, "
                f"{index['chunk_count']} chunks, {index['vector_count']} vectors, "
                f"{index['keyword_count']} lexical entries.",
                "",
            ]
        )
    for variant in result.get("variants", []):
        aggregate = variant["aggregate"]
        providers = variant["providers"]
        lines.extend(
            [
                f"## {variant['name']}",
                "",
                f"LLM `{providers['llm']['provider']}/{providers['llm']['model']}`; "
                f"embedding `{providers['embedding']['provider']}/"
                f"{providers['embedding']['model']}`; reranker "
                f"`{providers['reranker']['provider'] or 'off'}/"
                f"{providers['reranker']['model'] or 'none'}`; translation "
                f"`{providers['translation']['provider'] or 'off'}/"
                f"{providers['translation']['model'] or 'none'}`. Effective config hash "
                f"`{variant['effective_config']['configuration_hash']}`.",
                "",
                f"Passed {aggregate['passed']}/{aggregate['case_count']} "
                f"({aggregate['pass_rate']:.0%}); mean recall {aggregate['mean_recall']:.3f}; "
                f"p50/p95 {aggregate['latency_p50_ms']:.0f}/"
                f"{aggregate['latency_p95_ms']:.0f} ms.",
                "",
                "| Case | Tags | Result | Failure stage(s) | Total ms |",
                "|---|---|---:|---|---:|",
            ]
        )
        for case in variant["cases"]:
            stages = ", ".join(failure["stage"] for failure in case["failures"]) or "—"
            lines.append(
                f"| `{case['key']}` | {', '.join(case['tags'])} | "
                f"{'PASS' if case['passed'] else 'FAIL'} | {stages} | "
                f"{case['timings_ms']['total'] or 0} |"
            )
        lines.append("")
        failed_cases = [case for case in variant["cases"] if not case["passed"]]
        if failed_cases:
            lines.extend(["Failure details:", ""])
            for case in failed_cases:
                for failure in case["failures"]:
                    lines.append(f"- `{case['key']}` / `{failure['stage']}`: {failure['message']}")
            lines.append("")
    comparison = result.get("comparison")
    if comparison:
        overall = comparison["overall"]
        lines.extend(
            [
                "## Comparison",
                "",
                f"One-factor key: `{comparison['key']}`. Affected subsets: "
                f"{', '.join(comparison['affected_tags']) or 'none classified'}.",
                "",
                f"Overall pass-rate delta: {overall['pass_rate_delta']:+.1%}; "
                f"mean-recall delta: {overall['mean_recall_delta']:+.3f}; "
                f"p50 latency delta: {overall['latency_p50_ms_delta']:+.0f} ms.",
                "",
                "| Tag | Affected | Pass-rate delta | Recall delta | p50 delta ms |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for tag, metrics in comparison["tags"].items():
            lines.append(
                f"| {tag} | {'yes' if metrics['affected'] else 'no'} | "
                f"{metrics['pass_rate_delta']:+.1%} | {metrics['mean_recall_delta']:+.3f} | "
                f"{metrics['latency_p50_ms_delta']:+.0f} |"
            )
        lines.append("")
    if result["cleanup"].get("error"):
        lines.extend(["## Cleanup failure", "", str(result["cleanup"]["error"]), ""])
    lines.extend(
        [
            "## Notes",
            "",
            "Performance is descriptive for this local run. Configuration comparisons apply only "
            "to this `tax_v1` corpus and do not establish a universal production optimum.",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(result: dict[str, Any], artifact_dir: Path) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=False)
    sanitized = sanitize_diagnostics(result)
    (artifact_dir / "results.json").write_text(
        json.dumps(sanitized, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (artifact_dir / "summary.md").write_text(render_summary(sanitized), encoding="utf-8")


async def run_journey(
    settings: Settings,
    options: JourneyOptions,
    *,
    progress: Progress | None = None,
) -> tuple[dict[str, Any], Path]:
    """Execute one fresh tax_v1 journey and always emit a local report."""
    from app.core.config import JobQueueBackend
    from app.models.project import Project
    from app.platform.db.session import Database
    from app.platform.providers.implementations.storage_factory import create_storage_provider

    notify = progress or (lambda _message: None)
    if settings.jobs.backend is not JobQueueBackend.INLINE:
        raise JourneyError(
            "rag-journey requires process-local inline durable-job transport. "
            "Use the registered CLI command, which applies that transport override."
        )
    manifest = load_manifest(options.fixture)
    validate_safe_targets(
        settings,
        allow_nonlocal_database=options.allow_nonlocal_database,
        allow_nonlocal_storage=options.allow_nonlocal_storage,
    )
    run_uuid = uuid.uuid4()
    run_token = f"rag-journey:{run_uuid}"
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    artifact_dir = options.artifact_root / manifest.key / f"{timestamp}-{str(run_uuid)[:8]}"
    result: dict[str, Any] = {
        "schema_version": 1,
        "journey": manifest.key,
        "run_id": str(run_uuid),
        "started_at": datetime.now(UTC).isoformat(),
        "status": "failed",
        "project_id": None,
        "kept_project": options.keep_project,
        "job_transport": {
            "configured": options.configured_job_backend or "unknown",
            "harness": "inline",
            "scope": "this CLI process",
        },
        "index": None,
        "sources": {},
        "anchor_mappings": {},
        "variants": [],
        "comparison": None,
        "cleanup": {"status": "not_started"},
    }
    database = Database(settings)
    storage = create_storage_provider(settings)
    project_id: uuid.UUID | None = None
    document_ids: dict[str, uuid.UUID] = {}
    try:
        notify("preflight: database, migrations, pgvector, storage, default Organization")
        await database.check()
        await storage.check()
        async with database.session_factory() as session:
            await _preflight_default_organization(session)
            project = await _create_project(session, run_token=run_token)
            project_id = project.id
            result["project_id"] = str(project_id)

        baseline_values = dict(options.overrides)
        baseline_config = build_project_config(baseline_values)
        async with database.session_factory() as session:
            baseline_revision = await _activate_configuration(
                session,
                project_id=project_id,
                settings=settings,
                configuration=baseline_config,
                expected_revision_id=None,
                reason="tax_v1 baseline runtime configuration",
            )
        revision_ids = await _ingest_sources(
            database.session_factory,
            manifest=manifest,
            fixture_root=options.fixture.parent,
            project_id=project_id,
            settings=settings,
            storage=storage,
            progress=notify,
            document_ids=document_ids,
        )
        result["sources"] = {
            key: {
                "document_id": str(document_ids[key]),
                "source_revision_id": str(revision_ids[key]),
                "modifies": [
                    {
                        "source_key": target,
                        "target_revision_id": str(revision_ids[target]),
                        "target_provisions": next(
                            source for source in manifest.sources if source.key == key
                        ).modified_provisions.get(target, []),
                    }
                    for target in next(
                        source for source in manifest.sources if source.key == key
                    ).modifies
                ],
            }
            for key in document_ids
        }
        notify("verify: active vector/lexical index")
        result["index"] = await _ensure_indexed(
            database.session_factory,
            project_id=project_id,
            document_ids=document_ids,
            settings=settings,
        )
        chunks = await _runtime_chunks(
            database.session_factory,
            project_id=project_id,
            document_ids=document_ids,
        )
        anchor_mapping = resolve_evidence_anchors(manifest.anchors, chunks)
        result["anchor_mappings"] = {
            key: [str(chunk_id) for chunk_id in chunk_ids]
            for key, chunk_ids in anchor_mapping.items()
        }
        async with database.session_factory() as session:
            baseline_resolution = await _effective_resolution(
                session, project_id=project_id, settings=settings
            )
        baseline = await _run_variant(
            database.session_factory,
            name="baseline",
            manifest=manifest,
            project_id=project_id,
            document_ids=document_ids,
            anchor_mapping=anchor_mapping,
            settings=settings,
            resolution=baseline_resolution,
            progress=notify,
        )
        result["variants"].append(baseline)

        if options.comparison is not None:
            comparison_key, comparison_value = options.comparison
            comparison_values = {**baseline_values, comparison_key: comparison_value}
            comparison_config = build_project_config(comparison_values)
            async with database.session_factory() as session:
                await _activate_configuration(
                    session,
                    project_id=project_id,
                    settings=settings,
                    configuration=comparison_config,
                    expected_revision_id=baseline_revision.id,
                    reason=f"tax_v1 one-factor comparison: {comparison_key}",
                )
                comparison_resolution = await _effective_resolution(
                    session, project_id=project_id, settings=settings
                )
            if comparison_resolution.configuration_hash == baseline_resolution.configuration_hash:
                raise JourneyError(
                    f"--compare {comparison_key!r} does not change the effective runtime config."
                )
            comparison_index = await _ensure_indexed(
                database.session_factory,
                project_id=project_id,
                document_ids=document_ids,
                settings=settings,
            )
            if (
                comparison_index["id"] != result["index"]["id"]
                or comparison_index["corpus_fingerprint"] != result["index"]["corpus_fingerprint"]
            ):
                raise JourneyError(
                    "The one-factor comparison changed the active corpus index; "
                    "index-affecting comparisons are not supported."
                )
            comparison_variant = await _run_variant(
                database.session_factory,
                name=f"compare:{comparison_key}={comparison_value}",
                manifest=manifest,
                project_id=project_id,
                document_ids=document_ids,
                anchor_mapping=anchor_mapping,
                settings=settings,
                resolution=comparison_resolution,
                progress=notify,
            )
            result["variants"].append(comparison_variant)
            result["comparison"] = _comparison_summary(
                baseline,
                comparison_variant,
                key=comparison_key,
            )
        result["status"] = (
            "passed"
            if all(case["passed"] for variant in result["variants"] for case in variant["cases"])
            else "failed"
        )
    except Exception as exc:
        result["setup_error"] = f"{type(exc).__name__}: {exc}"
        notify(f"failure: {result['setup_error']}")
    finally:
        if project_id is None:
            result["cleanup"] = {"status": "not_needed"}
        elif options.keep_project:
            result["cleanup"] = {"status": "skipped_keep_project"}
        else:
            try:
                result["cleanup"] = {"status": "running"}
                await _cleanup_project(
                    database.session_factory,
                    project_id=project_id,
                    run_token=run_token,
                    document_ids=document_ids,
                    sources=manifest.sources,
                    settings=settings,
                    storage=storage,
                    progress=notify,
                )
                result["cleanup"] = {"status": "succeeded"}
            except Exception as exc:
                result["cleanup"] = {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                result["status"] = "failed"
        if project_id is not None and options.keep_project:
            async with database.session_factory() as session:
                if await session.get(Project, project_id) is None:
                    result["cleanup"] = {
                        "status": "failed",
                        "error": "--keep-project was set but the Project no longer exists.",
                    }
                    result["status"] = "failed"
        result["completed_at"] = datetime.now(UTC).isoformat()
        await database.dispose()
        write_reports(result, artifact_dir)
    return result, artifact_dir
