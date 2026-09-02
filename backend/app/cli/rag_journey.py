"""Small production-path RAG journey runner for the synthetic ``tax_v1`` fixture."""

from __future__ import annotations

import json
import math
import re
import statistics
import unicodedata
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import ResponseMode, Settings, StorageBackend
from app.modules.evaluation.metrics import rank_metrics
from app.modules.evaluation.schemas.evaluation import EvaluationCase, EvaluationCaseKind
from app.platform.config.profiles import execution_profile, execution_values
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
        "behavior.response_mode",
        "behavior.grounding_assurance",
        "behavior.domain_instructions",
        "behavior.translation_policy",
        "behavior.generation_model_id",
        "execution.profile_id",
        "execution.retrieval_top_k",
        "execution.semantic_candidate_top_k",
        "execution.keyword_candidate_top_k",
        "execution.hnsw_ef_search",
        "execution.rrf_k",
        "execution.semantic_weight",
        "execution.keyword_weight",
        "execution.rerank_mode",
        "execution.rerank_candidate_window",
        "execution.rerank_return_count",
        "execution.max_chunks_per_document",
        "execution.max_chunks_per_section",
        "execution.passage_scoring_enabled",
        "execution.passage_window_tokens",
        "execution.passage_overlap_tokens",
        "execution.passage_min_tokens",
        "execution.max_context_chunks",
        "execution.context_char_budget",
        "execution.max_history_messages",
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
    section: str | None = None
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
    required_anchor_groups: list[list[str]] = Field(default_factory=list)
    prohibited_final_sources: list[str] = Field(default_factory=list)
    prohibited_answer_tokens: list[str] = Field(default_factory=list)
    document_scope: str | None = None
    as_of: datetime | None = None
    expected_tokens: list[str] = Field(default_factory=list)
    expected_token_groups: list[list[str]] = Field(default_factory=list)
    expected_any: list[str] = Field(default_factory=list)
    user_parameter_tokens: list[str] = Field(default_factory=list)
    content_match_anchors: list[str] = Field(default_factory=list)
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
    compare_translation: bool = False
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
        grouped_anchors = {anchor for group in case.required_anchor_groups for anchor in group}
        unknown = (set(case.anchors) | grouped_anchors) - anchors
        if unknown:
            raise JourneyError(f"Case {case.key!r} references unknown anchors: {sorted(unknown)}")
        unknown_content_match = set(case.content_match_anchors) - anchors
        if unknown_content_match:
            raise JourneyError(
                f"Case {case.key!r} content_match_anchors reference unknown "
                f"anchors: {sorted(unknown_content_match)}"
            )
        if any(not group for group in case.required_anchor_groups):
            raise JourneyError(f"Case {case.key!r} contains an empty required anchor group.")
        if any(not group for group in case.expected_token_groups):
            raise JourneyError(f"Case {case.key!r} contains an empty expected token group.")
        unknown_sources = set(case.prohibited_final_sources) - sources
        if unknown_sources:
            raise JourneyError(
                f"Case {case.key!r} prohibits unknown sources: {sorted(unknown_sources)}"
            )
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
        normalized_section = normalize_text(anchor.section) if anchor.section else ""
        normalized_phrases = [normalize_text(phrase) for phrase in anchor.phrases]
        matches: list[RuntimeChunk] = []
        for chunk in chunks:
            if chunk.source_key != anchor.source:
                continue
            normalized_content = normalize_text(chunk.content)
            section_title = normalize_text(str(chunk.metadata.get("section_title") or ""))
            section_matches = not normalized_section or (
                normalized_section in section_title or normalized_section in normalized_content
            )
            if section_matches and all(
                phrase in normalized_content for phrase in normalized_phrases
            ):
                matches.append(chunk)
        # Phrase-only anchors target mixed/semantic documents whose chunk
        # boundaries are not stable. Require at least N matches rather than an
        # exact heading-sized cardinality.
        matched_count = len(matches)
        cardinality_ok = (
            matched_count >= anchor.expected_cardinality
            if not normalized_section
            else matched_count == anchor.expected_cardinality
        )
        if not cardinality_ok:
            identities = [f"{match.source_key}:{match.chunk_index}:{match.id}" for match in matches]
            expected = (
                f"at least {anchor.expected_cardinality}"
                if not normalized_section
                else str(anchor.expected_cardinality)
            )
            raise JourneyError(
                f"Anchor {anchor.key!r} expected {expected} chunk(s), "
                f"found {matched_count}: {identities}"
            )
        mappings[anchor.key] = [match.id for match in matches]
    return mappings


def _content_supporting_chunk_ids(
    *,
    anchor: EvidenceAnchor,
    chunks: Sequence[RuntimeChunk],
) -> set[str]:
    """Same-source chunks whose content contains the phrases or overlaps one that does.

    Phrase-only mixed documents can emit overlapping semantic chunks. The harness
    still requires evidence from the named source that actually contains or
    immediately neighbors the required phrases.
    """
    phrases = [normalize_text(phrase) for phrase in anchor.phrases]
    source_chunks = [chunk for chunk in chunks if chunk.source_key == anchor.source]
    phrase_indexes = {
        chunk.chunk_index
        for chunk in source_chunks
        if all(phrase in normalize_text(chunk.content) for phrase in phrases)
    }
    return {
        str(chunk.id)
        for chunk in source_chunks
        if chunk.chunk_index in phrase_indexes
        or any(abs(chunk.chunk_index - index) <= 1 for index in phrase_indexes)
    }


def _required_group_ids(
    group: Sequence[str],
    *,
    case: JourneyCase,
    anchor_mapping: Mapping[str, list[uuid.UUID]],
    anchors_by_key: Mapping[str, EvidenceAnchor],
    chunks: Sequence[RuntimeChunk],
) -> set[str]:
    ids: set[str] = set()
    for key in group:
        ids.update(str(chunk_id) for chunk_id in anchor_mapping.get(key, []))
        if key not in case.content_match_anchors:
            continue
        anchor = anchors_by_key.get(key)
        if anchor is None:
            continue
        ids.update(_content_supporting_chunk_ids(anchor=anchor, chunks=chunks))
    return ids


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
    execution = payload.get("execution")
    if isinstance(execution, dict) and execution.get("profile_id") is not None:
        # The journey is an explicit Test Lab path. Materialize candidate values
        # into its ephemeral Project revision so normal runtime readers never
        # need permission to activate an uncertified profile by ID.
        profile_id = str(execution.pop("profile_id"))
        profile_values = execution_values(execution_profile(profile_id, allow_candidate=True))
        payload["execution"] = {**profile_values, **execution}
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
        "original_dense",
        "original_lexical",
        "translated_dense",
        "translated_lexical",
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


_CONTEXT_SELECTION_REASONS = frozenset(
    {
        "authority_context_empty",
        "context_selection_empty",
    }
)


def _grounding_failure_stage(gate: Mapping[str, Any], reason: object) -> str:
    reason_value = getattr(reason, "value", reason)
    stage = gate.get("failure_stage")
    if stage == "context_selection" or reason_value in _CONTEXT_SELECTION_REASONS:
        return "context_selection"
    if stage == "retrieval" or reason_value == "no_retrieval_results":
        return "retrieval"
    if gate.get("generation_ran") and reason_value is None:
        return "generation_refusal"
    return "admission_grounding"


def _knowledge_document_ids(items: list[dict[str, Any]]) -> set[str]:
    return {
        str(item["document_id"])
        for item in items
        if item.get("document_id") and item.get("source_kind", "knowledge") == "knowledge"
    }


_TRANSLATED_BRANCH_PREFIXES = ("translated_dense", "translated_lexical")


def case_language_bucket(*, key: str, tags: list[str], query: str) -> str:
    """Classify a journey query by script/form for translation A/B slices."""
    tag_set = set(tags)
    if "codeswitch" in tag_set or "banglish" in key:
        return "banglish_codeswitch"
    has_bangla = any("\u0980" <= char <= "\u09ff" for char in query)
    has_latin = any("a" <= char.lower() <= "z" for char in query)
    if has_bangla and has_latin:
        return "mixed_language"
    if has_bangla:
        return "bangla"
    return "english"


def _optional_ms(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _ratio(numerator: object, denominator: object) -> float | None:
    if isinstance(numerator, bool) or isinstance(denominator, bool):
        return None
    if not isinstance(numerator, (int, float)) or not isinstance(denominator, (int, float)):
        return None
    if denominator <= 0:
        return None
    return float(numerator) / float(denominator)


def _has_translated_branch(item: Mapping[str, Any]) -> bool:
    if item.get("translated_dense") or item.get("translated_lexical"):
        return True
    provenance = item.get("branch_provenance") or {}
    if not isinstance(provenance, Mapping):
        return False
    return any(str(branch_id).startswith(_TRANSLATED_BRANCH_PREFIXES) for branch_id in provenance)


def _first_relevant_rank(retrieved: list[dict[str, Any]], relevant_ids: set[str]) -> int | None:
    for rank, item in enumerate(retrieved, start=1):
        chunk_id = item.get("chunk_id")
        if chunk_id is not None and str(chunk_id) in relevant_ids:
            return rank
    return None


def evaluate_case_result(
    *,
    case: JourneyCase,
    message: Any,
    anchor_mapping: Mapping[str, list[uuid.UUID]],
    document_ids: Mapping[str, uuid.UUID],
    response_mode: ResponseMode,
    modifies_expansion_enabled: bool,
    chunks: Sequence[RuntimeChunk] = (),
    anchors: Sequence[EvidenceAnchor] = (),
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
    anchors_by_key = {anchor.key: anchor for anchor in anchors}
    relevant_ids = {
        str(chunk_id) for anchor in case.anchors for chunk_id in anchor_mapping.get(anchor, [])
    }
    content_match_ids = _required_group_ids(
        case.content_match_anchors,
        case=case,
        anchor_mapping=anchor_mapping,
        anchors_by_key=anchors_by_key,
        chunks=chunks,
    )
    result_ids = [str(item.get("chunk_id")) for item in retrieved if item.get("chunk_id")]
    recall, reciprocal_rank, ndcg, relevant_retrieved = rank_metrics(result_ids, relevant_ids)
    admitted_ids = {str(item.get("chunk_id")) for item in admitted if item.get("chunk_id")}
    citation_ids = {
        str(item["chunk_id"])
        for item in citations
        if item["source_kind"] == "knowledge" and item.get("chunk_id")
    }
    claim_evidence_ids = {
        str(item["chunk_id"])
        for claim in claims
        for item in claim["evidence"]
        if item["source_kind"] == "knowledge" and item.get("chunk_id")
    }
    normalized_answer = normalize_text(message.content)
    web = dict(metadata.get("web_search") or {})
    gate = dict(metadata.get("evidence_gate") or {})
    gate_admitted_ids = {
        str(item["chunk_id"])
        for item in list((gate.get("candidate_wise") or {}).get("assessments") or [])
        if item.get("passed") and item.get("chunk_id")
    }
    if gate_admitted_ids:
        admitted_ids = gate_admitted_ids
    authority = dict(metadata.get("current_authority") or {})
    scope_current_authority = dict(metadata.get("scope_current_authority") or {})
    failures: list[dict[str, str]] = []
    all_knowledge_evidence = candidates + retrieved + admitted + citations
    finalized_knowledge_evidence = admitted + citations
    for claim in claims:
        all_knowledge_evidence.extend(claim["evidence"])
        finalized_knowledge_evidence.extend(claim["evidence"])

    prohibited_documents = {str(document_ids[source]) for source in case.prohibited_final_sources}
    leaked_prohibited = _knowledge_document_ids(finalized_knowledge_evidence) & prohibited_documents
    if leaked_prohibited:
        failures.append(
            _failure(
                "authority",
                f"Final evidence used prohibited sources: {sorted(leaked_prohibited)}.",
            )
        )

    if "codeswitch" in case.tags:
        translation = dict(trace.get("translation") or {})
        if not translation.get("query_language_profile"):
            failures.append(
                _failure(
                    "retrieval",
                    "Code-switched query is missing language/translation diagnostics.",
                )
            )

    if case.document_scope is not None:
        expected_document = str(document_ids[case.document_scope])
        leaked = _knowledge_document_ids(all_knowledge_evidence) - {expected_document}
        if leaked:
            failures.append(
                _failure("authority", f"Hard document scope leaked evidence from {sorted(leaked)}.")
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
        for token in case.user_parameter_tokens:
            if normalize_text(token) not in normalized_answer:
                failures.append(
                    _failure(
                        "generation_refusal",
                        f"Answer is missing user-provided parameter {token!r}.",
                    )
                )
        for group in case.expected_token_groups:
            if not any(_contains_semantic_marker(normalized_answer, token) for token in group):
                failures.append(
                    _failure(
                        "generation_refusal",
                        "Answer is missing an expected fact equivalent to "
                        f"{group[0]!r} (accepted equivalents: {group!r}).",
                    )
                )
        for token in case.prohibited_answer_tokens:
            normalized_token = normalize_text(token)
            if f" {normalized_token} " in f" {normalized_answer} ":
                failures.append(
                    _failure(
                        "generation_refusal",
                        f"Answer contains prohibited stale/example fact {token!r}.",
                    )
                )
        for group in case.required_anchor_groups:
            group_ids = _required_group_ids(
                group,
                case=case,
                anchor_mapping=anchor_mapping,
                anchors_by_key=anchors_by_key,
                chunks=chunks,
            )
            label = " or ".join(group)
            if not group_ids & set(result_ids):
                failures.append(
                    _failure("retrieval", f"Required evidence group was not retrieved: {label}.")
                )
            if not group_ids & admitted_ids:
                failures.append(
                    _failure(
                        "admission_grounding",
                        f"Required evidence group was not admitted: {label}.",
                    )
                )
            grounded_or_cited_ids = claim_evidence_ids
            if any(key in case.content_match_anchors for key in group):
                grounded_or_cited_ids = claim_evidence_ids | citation_ids
            if not group_ids & grounded_or_cited_ids:
                failures.append(
                    _failure(
                        "citation",
                        f"No grounded claim used required evidence group: {label}.",
                    )
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
            failures.append(
                _failure(
                    _grounding_failure_stage(gate, message.insufficient_evidence_reason),
                    "Answerable case was not grounded.",
                )
            )
        if not bool(gate.get("sufficient")):
            failures.append(
                _failure(
                    _grounding_failure_stage(gate, message.insufficient_evidence_reason),
                    "Indexed evidence did not pass the grounding gate.",
                )
            )
        citation_relevant_ids = relevant_ids | content_match_ids
        if citation_relevant_ids and not (citation_ids & citation_relevant_ids):
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

    rerank = sanitize_diagnostics(trace.get("rerank") or {})
    provider_degradation = None
    if rerank.get("status") == "unavailable":
        provider_degradation = {
            "component": "rerank",
            "status": "unavailable",
            "failure_reason": rerank.get("failure_reason") or "unavailable",
        }

    failure_stages = {failure["stage"] for failure in failures}
    expected_admitted_ids = admitted_ids & relevant_ids
    winning = retrieved[0] if retrieved else None
    translation = sanitize_diagnostics(trace.get("translation") or {})
    retrieval_ms = _optional_ms(message.retrieval_latency_ms)
    generation_ms = _optional_ms(message.provider_latency_ms)
    total_ms = _optional_ms(message.total_latency_ms)
    translation_ms = _optional_ms(translation.get("latency_ms"))
    rerank_ms = _optional_ms(rerank.get("latency_ms"))
    residual_ms = None
    if retrieval_ms is not None and generation_ms is not None and total_ms is not None:
        residual_ms = max(0, total_ms - retrieval_ms - generation_ms)

    return {
        "key": case.key,
        "tags": case.tags,
        "mode": case.mode,
        "language_bucket": case_language_bucket(key=case.key, tags=case.tags, query=case.query),
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
            "relevant_rank": _first_relevant_rank(retrieved, relevant_ids),
            "relevant_retrieved_ids": [
                result_id for result_id in result_ids if result_id in relevant_ids
            ],
        },
        "admitted": admitted,
        "citations": citations,
        "claims": claims,
        "authority": sanitize_diagnostics(authority),
        "evidence_gate": sanitize_diagnostics(gate),
        "fallback": sanitize_diagnostics(web),
        "translation": translation,
        "executed_branches": list(trace.get("executed_branches") or []),
        "branch_candidate_counts": dict(trace.get("branch_candidate_counts") or {}),
        "rerank": rerank,
        "provider_degradation": provider_degradation,
        "quality": {
            "expected_evidence_admitted": bool(expected_admitted_ids),
            "expected_evidence_admitted_count": len(expected_admitted_ids),
            "grounding_success": (
                "admission_grounding" not in failure_stages
                and "context_selection" not in failure_stages
                and bool(message.grounded)
            ),
            "citation_correctness": "citation" not in failure_stages,
            "generation_refusal_correctness": "generation_refusal" not in failure_stages,
            "winning_chunk_id": winning.get("chunk_id") if winning else None,
            "winning_document_id": winning.get("document_id") if winning else None,
            "translation_applied": translation.get("status") in {"applied", "failed"},
            "translation_contributed_to_winning": (
                winning is not None and _has_translated_branch(winning)
            ),
            "translation_contributed_to_admitted": any(
                _has_translated_branch(item) for item in admitted
            ),
        },
        "timings_ms": {
            "translation": translation_ms,
            "dense": None,
            "lexical": None,
            "rerank": rerank_ms,
            "retrieval": retrieval_ms,
            "grounding_and_context": residual_ms,
            "generation": generation_ms,
            "total": total_ms,
            "translation_share": _ratio(translation_ms, total_ms),
        },
    }


def aggregate_results(cases: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [
        float(case["timings_ms"]["total"])
        for case in cases
        if isinstance(case.get("timings_ms", {}).get("total"), (int, float))
    ]
    passed = sum(bool(case["passed"]) for case in cases)
    failure_counts = {
        stage: sum(
            failure["stage"] == stage for case in cases for failure in case.get("failures", [])
        )
        for stage in (
            "retrieval",
            "authority",
            "admission_grounding",
            "context_selection",
            "generation_refusal",
            "citation",
            "fallback",
        )
    }
    degradation_reasons: dict[str, int] = {}
    for case in cases:
        degradation = case.get("provider_degradation") or {}
        if degradation.get("component") != "rerank":
            continue
        reason = str(degradation.get("failure_reason") or "unavailable")
        degradation_reasons[reason] = degradation_reasons.get(reason, 0) + 1
    return {
        "case_count": len(cases),
        "passed": passed,
        "failed": len(cases) - passed,
        "pass_rate": passed / len(cases) if cases else 0.0,
        "mean_recall": statistics.fmean(
            float(case["retrieval"]["recall"]) for case in cases if case["mode"] == "answerable"
        )
        if any(case["mode"] == "answerable" for case in cases)
        else 0.0,
        "latency_p50_ms": statistics.median(latencies) if latencies else 0.0,
        "latency_p95_ms": _percentile(latencies, 0.95),
        "latency_mean_ms": statistics.fmean(latencies) if latencies else 0.0,
        "latency_max_ms": max(latencies) if latencies else 0.0,
        "failure_counts": failure_counts,
        "correctness": {
            "passed": passed,
            "failed": len(cases) - passed,
            "pass_rate": passed / len(cases) if cases else 0.0,
            "failure_counts": failure_counts,
        },
        "provider_degradation": {
            "rerank_unavailable_count": sum(degradation_reasons.values()),
            "by_failure_reason": dict(sorted(degradation_reasons.items())),
        },
        "latency": {
            "p50_ms": statistics.median(latencies) if latencies else 0.0,
            "p95_ms": _percentile(latencies, 0.95),
            "mean_ms": statistics.fmean(latencies) if latencies else 0.0,
            "max_ms": max(latencies) if latencies else 0.0,
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
        schema_version=revision.schema_version,
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
    elif "codeswitch" in case.tags:
        kind = EvaluationCaseKind.CODE_SWITCHED
    elif "multilingual" in case.tags:
        kind = EvaluationCaseKind.CROSS_LINGUAL
    elif case.document_scope:
        kind = EvaluationCaseKind.METADATA_FILTER
    else:
        kind = EvaluationCaseKind.CITATION
    if "codeswitch" in case.tags:
        query_form = "code_switched"
    elif "banglish" in case.key:
        query_form = "banglish"
    else:
        query_form = None
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
        query_language=("bn" if any("\u0980" <= char <= "\u09ff" for char in case.query) else None),
        expected_evidence_language=None,
        query_form=query_form,
    )


async def _run_variant(
    session_factory: Any,
    *,
    name: str,
    manifest: JourneyManifest,
    project_id: uuid.UUID,
    document_ids: Mapping[str, uuid.UUID],
    anchor_mapping: Mapping[str, list[uuid.UUID]],
    chunks: Sequence[RuntimeChunk],
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
                chunks=chunks,
                anchors=manifest.anchors,
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
        settings,
        _active_record(revision),
        validate_web_provider=False,
        allow_candidate_profiles=True,
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


async def _await_document_purge(
    session_factory: Any,
    *,
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    settings: Settings,
    timeout_seconds: float = 900.0,
) -> None:
    """Drain inline durable purge jobs through retry and terminal states."""
    import asyncio
    from datetime import UTC, datetime

    from app.composition.jobs import build_job_service
    from app.models.job_run import JobState
    from app.platform.jobs.implementations.inline_queue import InlineJobQueue

    started = datetime.now(UTC)
    while True:
        async with session_factory() as session:
            service = build_job_service(
                session=session,
                project_id=project_id,
                settings=settings,
                queue=InlineJobQueue(),
            )
            run = (await service.get_detail(job_id)).run
            if run.state is JobState.SUCCEEDED:
                return
            if run.state is JobState.FAILED:
                raise JourneyError(
                    "Document purge job failed "
                    f"({run.failure_code or 'unknown'}: {run.failure_message or 'no message'})."
                )
            if run.state in {
                JobState.RETRY_SCHEDULED,
                JobState.QUEUED,
                JobState.RUNNING,
            }:
                await service.dispatch(job_id)
            else:
                await service.dispatch_next()
        elapsed = (datetime.now(UTC) - started).total_seconds()
        if elapsed >= timeout_seconds:
            raise JourneyError(
                f"Document purge job {job_id} did not finish within {timeout_seconds:.0f}s."
            )
        await asyncio.sleep(0.1)


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
            document = await service.purge(document_id)
            job_id = getattr(document, "job_id", None)
            if job_id is None:
                raise JourneyError(f"Document purge did not return a job id for {source_key}.")
        await _await_document_purge(
            session_factory,
            project_id=project_id,
            job_id=job_id,
            settings=settings,
        )
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
    if key == "behavior.translation_policy":
        affected.append("multilingual")
    if key.startswith("behavior.response_mode"):
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


def _admitted_chunk_ids(case: Mapping[str, Any]) -> set[str]:
    return {
        str(item["chunk_id"]) for item in list(case.get("admitted") or []) if item.get("chunk_id")
    }


def _quality_snapshot(case: Mapping[str, Any]) -> dict[str, Any]:
    quality = dict(case.get("quality") or {})
    retrieval = dict(case.get("retrieval") or {})
    return {
        "passed": bool(case.get("passed")),
        "recall": float(retrieval.get("recall") or 0.0),
        "reciprocal_rank": float(retrieval.get("reciprocal_rank") or 0.0),
        "ndcg": float(retrieval.get("ndcg") or 0.0),
        "relevant_rank": retrieval.get("relevant_rank"),
        "expected_evidence_admitted": bool(quality.get("expected_evidence_admitted")),
        "expected_evidence_admitted_count": int(
            quality.get("expected_evidence_admitted_count") or 0
        ),
        "grounding_success": bool(quality.get("grounding_success")),
        "citation_correctness": bool(quality.get("citation_correctness")),
        "generation_refusal_correctness": bool(quality.get("generation_refusal_correctness")),
        "winning_chunk_id": quality.get("winning_chunk_id"),
        "winning_document_id": quality.get("winning_document_id"),
    }


def _strictly_better_quality(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Return True when left is strictly better on the ordered quality tuple."""
    left_rank = left.get("relevant_rank")
    right_rank = right.get("relevant_rank")
    left_rank_score = 0 if left_rank is None else -int(left_rank)
    right_rank_score = 0 if right_rank is None else -int(right_rank)
    left_tuple = (
        int(bool(left.get("passed"))),
        float(left.get("recall") or 0.0),
        float(left.get("reciprocal_rank") or 0.0),
        float(left.get("ndcg") or 0.0),
        int(bool(left.get("expected_evidence_admitted"))),
        int(bool(left.get("grounding_success"))),
        int(bool(left.get("citation_correctness"))),
        int(bool(left.get("generation_refusal_correctness"))),
        left_rank_score,
    )
    right_tuple = (
        int(bool(right.get("passed"))),
        float(right.get("recall") or 0.0),
        float(right.get("reciprocal_rank") or 0.0),
        float(right.get("ndcg") or 0.0),
        int(bool(right.get("expected_evidence_admitted"))),
        int(bool(right.get("grounding_success"))),
        int(bool(right.get("citation_correctness"))),
        int(bool(right.get("generation_refusal_correctness"))),
        right_rank_score,
    )
    comparable_left = (
        left_tuple[0],
        round(left_tuple[1], 9),
        round(left_tuple[2], 9),
        round(left_tuple[3], 9),
        *left_tuple[4:],
    )
    comparable_right = (
        right_tuple[0],
        round(right_tuple[1], 9),
        round(right_tuple[2], 9),
        round(right_tuple[3], 9),
        *right_tuple[4:],
    )
    return comparable_left > comparable_right


def translation_contribution_label(case: Mapping[str, Any]) -> str:
    quality = dict(case.get("quality") or {})
    winning = bool(quality.get("translation_contributed_to_winning"))
    admitted = bool(quality.get("translation_contributed_to_admitted"))
    if winning and admitted:
        return "winning+admitted"
    if admitted:
        return "admitted"
    if winning:
        return "winning"
    return "none"


def translation_changed_retrieval_outcome(
    on_case: Mapping[str, Any],
    off_case: Mapping[str, Any],
) -> dict[str, Any]:
    """Describe whether translation changed retrieval/admission, not just that it ran."""
    on_relevant = set((on_case.get("retrieval") or {}).get("relevant_retrieved_ids") or [])
    off_relevant = set((off_case.get("retrieval") or {}).get("relevant_retrieved_ids") or [])
    on_admitted = _admitted_chunk_ids(on_case)
    off_admitted = _admitted_chunk_ids(off_case)
    on_quality = _quality_snapshot(on_case)
    off_quality = _quality_snapshot(off_case)
    on_relevant_rank = on_quality.get("relevant_rank")
    off_relevant_rank = off_quality.get("relevant_rank")
    introduced_relevant = bool(on_relevant - off_relevant)
    improved_rank = (
        isinstance(on_relevant_rank, int)
        and isinstance(off_relevant_rank, int)
        and on_relevant_rank < off_relevant_rank
    )
    changed_winning = on_quality.get("winning_chunk_id") != off_quality.get("winning_chunk_id")
    changed_admitted = on_admitted != off_admitted
    if introduced_relevant:
        summary = "introduced_relevant"
    elif improved_rank:
        summary = "improved_rank"
    elif changed_admitted:
        summary = "changed_admitted"
    elif changed_winning:
        summary = "changed_winning"
    else:
        summary = "none"
    return {
        "introduced_relevant_candidate": introduced_relevant,
        "improved_relevant_rank": improved_rank,
        "changed_winning_evidence": changed_winning,
        "changed_admitted_evidence": changed_admitted,
        "meaningful": summary != "none",
        "summary": summary,
    }


def classify_translation_verdict(
    on_case: Mapping[str, Any],
    off_case: Mapping[str, Any],
) -> str:
    on_quality = _quality_snapshot(on_case)
    off_quality = _quality_snapshot(off_case)
    if on_quality["passed"] and not off_quality["passed"]:
        return "required"
    if off_quality["passed"] and not on_quality["passed"]:
        return "harmful"
    if _strictly_better_quality(on_quality, off_quality):
        return "helpful"
    if _strictly_better_quality(off_quality, on_quality):
        return "harmful"
    return "no_material_benefit"


def _latency_distribution(values: list[float]) -> dict[str, float]:
    return {
        "p50_ms": statistics.median(values) if values else 0.0,
        "p95_ms": _percentile(values, 0.95),
        "mean_ms": statistics.fmean(values) if values else 0.0,
        "count": float(len(values)),
    }


def _timing_slice(cases: Sequence[Mapping[str, Any]], field: str) -> list[float]:
    values: list[float] = []
    for case in cases:
        value = _optional_ms((case.get("timings_ms") or {}).get(field))
        if value is not None:
            values.append(float(value))
    return values


def _share_slice(cases: Sequence[Mapping[str, Any]]) -> list[float]:
    values: list[float] = []
    for case in cases:
        share = (case.get("timings_ms") or {}).get("translation_share")
        if isinstance(share, (int, float)) and not isinstance(share, bool):
            values.append(float(share))
    return values


def _paired_latency_block(
    on_cases: Sequence[Mapping[str, Any]],
    off_cases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    on_total = _timing_slice(on_cases, "total")
    off_total = _timing_slice(off_cases, "total")
    on_translation = _timing_slice(on_cases, "translation")
    shares = _share_slice(on_cases)
    on_sum = sum(on_total)
    translation_sum = sum(on_translation)
    return {
        "on": {
            "total": _latency_distribution(on_total),
            "translation": _latency_distribution(on_translation),
            "retrieval": _latency_distribution(_timing_slice(on_cases, "retrieval")),
            "rerank": _latency_distribution(_timing_slice(on_cases, "rerank")),
            "grounding_and_context": _latency_distribution(
                _timing_slice(on_cases, "grounding_and_context")
            ),
            "generation": _latency_distribution(_timing_slice(on_cases, "generation")),
        },
        "off": {
            "total": _latency_distribution(off_total),
            "retrieval": _latency_distribution(_timing_slice(off_cases, "retrieval")),
            "rerank": _latency_distribution(_timing_slice(off_cases, "rerank")),
            "grounding_and_context": _latency_distribution(
                _timing_slice(off_cases, "grounding_and_context")
            ),
            "generation": _latency_distribution(_timing_slice(off_cases, "generation")),
        },
        "delta_total_p50_ms": (
            (_latency_distribution(on_total)["p50_ms"] - _latency_distribution(off_total)["p50_ms"])
            if on_total or off_total
            else 0.0
        ),
        "delta_total_p95_ms": (
            (_latency_distribution(on_total)["p95_ms"] - _latency_distribution(off_total)["p95_ms"])
            if on_total or off_total
            else 0.0
        ),
        "delta_total_mean_ms": (
            (
                _latency_distribution(on_total)["mean_ms"]
                - _latency_distribution(off_total)["mean_ms"]
            )
            if on_total or off_total
            else 0.0
        ),
        "translation_share": {
            "p50": statistics.median(shares) if shares else 0.0,
            "p95": _percentile(shares, 0.95),
            "mean": statistics.fmean(shares) if shares else 0.0,
            "overall": (translation_sum / on_sum) if on_sum else 0.0,
        },
    }


def build_translation_comparison(
    on_variant: Mapping[str, Any],
    off_variant: Mapping[str, Any],
) -> dict[str, Any]:
    """Pair ON/OFF cases from one shared corpus index without a second harness."""
    off_by_key = {case["key"]: case for case in off_variant.get("cases") or []}
    pairs: list[dict[str, Any]] = []
    for on_case in on_variant.get("cases") or []:
        off_case = off_by_key.get(on_case["key"])
        if off_case is None:
            continue
        outcome = translation_changed_retrieval_outcome(on_case, off_case)
        on_ms = _optional_ms((on_case.get("timings_ms") or {}).get("total")) or 0
        off_ms = _optional_ms((off_case.get("timings_ms") or {}).get("total")) or 0
        pairs.append(
            {
                "key": on_case["key"],
                "language_bucket": on_case.get("language_bucket")
                or case_language_bucket(
                    key=str(on_case["key"]),
                    tags=list(on_case.get("tags") or []),
                    query="",
                ),
                "on_passed": bool(on_case.get("passed")),
                "off_passed": bool(off_case.get("passed")),
                "on_ms": on_ms,
                "off_ms": off_ms,
                "delta_ms": on_ms - off_ms,
                "retrieval_delta": outcome["summary"],
                "translation_contribution": translation_contribution_label(on_case),
                "verdict": classify_translation_verdict(on_case, off_case),
                "translation_changed_retrieval_outcome": outcome,
                "quality": {
                    "on": _quality_snapshot(on_case),
                    "off": _quality_snapshot(off_case),
                },
                "timings_ms": {
                    "on": dict(on_case.get("timings_ms") or {}),
                    "off": dict(off_case.get("timings_ms") or {}),
                },
            }
        )

    on_cases = list(on_variant.get("cases") or [])
    off_cases = list(off_variant.get("cases") or [])
    applicable_keys = {
        case["key"] for case in on_cases if (case.get("quality") or {}).get("translation_applied")
    }

    def _select(
        cases: Sequence[Mapping[str, Any]],
        predicate: Callable[[Mapping[str, Any]], bool],
    ) -> list[Mapping[str, Any]]:
        return [case for case in cases if predicate(case)]

    def _in_bucket(current: str) -> Callable[[Mapping[str, Any]], bool]:
        return lambda case: case.get("language_bucket") == current

    latency = {
        "all": _paired_latency_block(on_cases, off_cases),
        "translation_applicable": _paired_latency_block(
            _select(on_cases, lambda case: case["key"] in applicable_keys),
            _select(off_cases, lambda case: case["key"] in applicable_keys),
        ),
    }
    for bucket in ("bangla", "banglish_codeswitch", "mixed_language", "english"):
        latency[bucket] = _paired_latency_block(
            _select(on_cases, _in_bucket(bucket)),
            _select(off_cases, _in_bucket(bucket)),
        )

    verdicts = {
        label: [pair["key"] for pair in pairs if pair["verdict"] == label]
        for label in ("required", "helpful", "no_material_benefit", "harmful")
    }
    overhead = [
        pair["key"]
        for pair in pairs
        if pair["verdict"] == "no_material_benefit"
        and pair["translation_contribution"] == "none"
        and not pair["translation_changed_retrieval_outcome"]["meaningful"]
        and pair["key"] in applicable_keys
    ]
    return {
        "kind": "query_translation",
        "on_variant": on_variant.get("name"),
        "off_variant": off_variant.get("name"),
        "cases": pairs,
        "summary": {
            "verdict_counts": {label: len(keys) for label, keys in verdicts.items()},
            "required_cases": verdicts["required"],
            "helpful_cases": verdicts["helpful"],
            "no_material_benefit_cases": verdicts["no_material_benefit"],
            "harmful_cases": verdicts["harmful"],
            "pure_overhead_cases": overhead,
            "on_passed": sum(bool(pair["on_passed"]) for pair in pairs),
            "off_passed": sum(bool(pair["off_passed"]) for pair in pairs),
            "case_count": len(pairs),
        },
        "latency": latency,
    }


def translation_variants(
    variants: list[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    enabled: Mapping[str, Any] | None = None
    disabled: Mapping[str, Any] | None = None
    for variant in variants:
        configuration = (variant.get("effective_config") or {}).get("configuration") or {}
        retrieval = dict(configuration.get("retrieval") or {})
        if retrieval.get("query_translation_enabled") is True:
            enabled = variant
        elif retrieval.get("query_translation_enabled") is False:
            disabled = variant
    if enabled is None or disabled is None:
        return None
    return enabled, disabled


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
        correctness = aggregate.get("correctness") or {}
        degradation = aggregate.get("provider_degradation") or {}
        latency = aggregate.get("latency") or {}
        reason_parts = [
            f"{reason} {count}"
            for reason, count in (degradation.get("by_failure_reason") or {}).items()
        ]
        reason_text = ", ".join(reason_parts) if reason_parts else "none"
        failure_parts = [
            f"{stage} {count}"
            for stage, count in (
                correctness.get("failure_counts") or aggregate["failure_counts"]
            ).items()
            if count
        ]
        lines.extend(
            [
                "### Correctness",
                "",
                f"Passed {correctness.get('passed', aggregate['passed'])}/"
                f"{aggregate['case_count']}; semantic RAG failures: "
                f"{', '.join(failure_parts) or 'none'}.",
                "",
                "### Provider degradation",
                "",
                f"Rerank unavailable: {degradation.get('rerank_unavailable_count', 0)} "
                f"({reason_text}). Provider timeouts, rate limits, and connection "
                "failures are reported here and do not count as semantic RAG failures.",
                "",
                "### Latency",
                "",
                f"p50/p95/mean/max "
                f"{latency.get('p50_ms', aggregate['latency_p50_ms']):.0f}/"
                f"{latency.get('p95_ms', aggregate['latency_p95_ms']):.0f}/"
                f"{latency.get('mean_ms', 0):.0f}/"
                f"{latency.get('max_ms', 0):.0f} ms.",
                "",
            ]
        )
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
    translation = result.get("translation_comparison")
    if translation:
        lines.extend(_render_translation_comparison(translation))
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


_LANGUAGE_BUCKET_LABELS = {
    "bangla": "bn",
    "banglish_codeswitch": "banglish",
    "mixed_language": "mixed",
    "english": "en",
}


def _fmt_ms(value: object) -> str:
    number = _optional_ms(value)
    return "—" if number is None else str(number)


def _fmt_share(value: object) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "—"
    return f"{float(value):.0%}"


def _render_latency_row(label: str, block: Mapping[str, Any]) -> str:
    on_total = dict((block.get("on") or {}).get("total") or {})
    off_total = dict((block.get("off") or {}).get("total") or {})
    share = dict(block.get("translation_share") or {})
    return (
        f"| {label} | {on_total.get('p50_ms', 0):.0f}/{on_total.get('p95_ms', 0):.0f}/"
        f"{on_total.get('mean_ms', 0):.0f} | {off_total.get('p50_ms', 0):.0f}/"
        f"{off_total.get('p95_ms', 0):.0f}/{off_total.get('mean_ms', 0):.0f} | "
        f"{block.get('delta_total_p50_ms', 0):+.0f}/{block.get('delta_total_p95_ms', 0):+.0f}/"
        f"{block.get('delta_total_mean_ms', 0):+.0f} | "
        f"{_fmt_share(share.get('p50'))}/{_fmt_share(share.get('overall'))} |"
    )


def _render_translation_comparison(translation: Mapping[str, Any]) -> list[str]:
    summary = dict(translation.get("summary") or {})
    latency = dict(translation.get("latency") or {})
    counts = dict(summary.get("verdict_counts") or {})
    lines = [
        "## Translation A/B",
        "",
        "Same Project, corpus, and active index. `translation_on` is the current configuration; "
        "`translation_off` only sets `behavior.translation_policy=disabled`. "
        "Quality uses journey pass/fail, recall, rank, nDCG, admission, grounding, citation, and "
        "generation — not LLM wording similarity. `grounding_and_context` is residual "
        "`total - retrieval - generation`. Dense/lexical branch latencies are omitted unless "
        "the retrieval diagnostics expose them.",
        "",
        "| Case | Lang | ON | OFF | ON ms | OFF ms | Δ ms | Retrieval Δ "
        "| Translation contribution | Verdict |",
        "|---|---|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for pair in translation.get("cases") or []:
        lang = _LANGUAGE_BUCKET_LABELS.get(
            str(pair.get("language_bucket")),
            pair.get("language_bucket") or "—",
        )
        lines.append(
            f"| `{pair['key']}` | {lang} | "
            f"{'PASS' if pair.get('on_passed') else 'FAIL'} | "
            f"{'PASS' if pair.get('off_passed') else 'FAIL'} | "
            f"{_fmt_ms(pair.get('on_ms'))} | {_fmt_ms(pair.get('off_ms'))} | "
            f"{int(pair.get('delta_ms') or 0):+d} | "
            f"{str(pair.get('retrieval_delta') or 'none').replace('_', ' ')} | "
            f"{pair.get('translation_contribution') or 'none'} | "
            f"{str(pair.get('verdict') or '').replace('_', ' ')} |"
        )
    lines.extend(
        [
            "",
            "### Quality",
            "",
            f"ON passed {summary.get('on_passed', 0)}/{summary.get('case_count', 0)}; "
            f"OFF passed {summary.get('off_passed', 0)}/{summary.get('case_count', 0)}. "
            f"Verdicts: required {counts.get('required', 0)}, helpful {counts.get('helpful', 0)}, "
            f"no material benefit {counts.get('no_material_benefit', 0)}, "
            f"harmful {counts.get('harmful', 0)}.",
            "",
            "Genuinely necessary (`required`): "
            f"{', '.join(f'`{key}`' for key in summary.get('required_cases') or []) or 'none'}.",
            "",
            "Pure overhead (applied translation, no retrieval/quality change): "
            + (", ".join(f"`{key}`" for key in summary.get("pure_overhead_cases") or []) or "none")
            + ".",
            "",
            "Harmful: "
            f"{', '.join(f'`{key}`' for key in summary.get('harmful_cases') or []) or 'none'}.",
            "",
            "### Latency",
            "",
            "| Slice | ON p50/p95/mean | OFF p50/p95/mean | Δ p50/p95/mean "
            "| Translation share p50/overall |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for label, key in (
        ("all cases", "all"),
        ("translated/applicable", "translation_applicable"),
        ("Bangla", "bangla"),
        ("Banglish/code-switch", "banglish_codeswitch"),
        ("mixed-language", "mixed_language"),
        ("ordinary English", "english"),
    ):
        block = latency.get(key)
        if isinstance(block, Mapping):
            lines.append(_render_latency_row(label, block))
    lines.append("")
    return lines


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
        "translation_comparison": None,
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
            name="translation_on" if options.compare_translation else "baseline",
            manifest=manifest,
            project_id=project_id,
            document_ids=document_ids,
            anchor_mapping=anchor_mapping,
            chunks=chunks,
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
                    reason=(
                        "tax_v1 translation on/off comparison"
                        if options.compare_translation
                        else f"tax_v1 one-factor comparison: {comparison_key}"
                    ),
                )
                comparison_resolution = await _effective_resolution(
                    session, project_id=project_id, settings=settings
                )
            if comparison_resolution.configuration_hash == baseline_resolution.configuration_hash:
                if options.compare_translation:
                    raise JourneyError(
                        "--compare-translation requires query translation to be enabled on the "
                        "current configuration so the OFF variant can disable it."
                    )
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
            comparison_name = (
                "translation_off"
                if options.compare_translation
                else f"compare:{comparison_key}={comparison_value}"
            )
            comparison_variant = await _run_variant(
                database.session_factory,
                name=comparison_name,
                manifest=manifest,
                project_id=project_id,
                document_ids=document_ids,
                anchor_mapping=anchor_mapping,
                chunks=chunks,
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
            paired = translation_variants(result["variants"])
            if paired is not None:
                result["translation_comparison"] = build_translation_comparison(*paired)
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
