"""One-shot pre-public configuration reset helpers.

This module is intentionally outside the runtime resolver. It translates the
last legacy Project payloads and rewrites persisted snapshots before the V2-only
runtime starts.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.core.config import LLMBackend, RerankMode, Settings
from app.core.generation_models import (
    generation_model_id_for_legacy_pair,
    generation_model_policy,
)
from app.platform.config.project_ai import (
    CanonicalRerankMode,
    InvariantState,
    ProjectAIConfig,
    ProjectBehaviorV2,
    ProjectExecutionV2,
    TranslationPolicy,
)
from app.platform.jobs.contracts import JobConfiguration

RETIRED_RETRIEVAL_KEYS = frozenset(
    {
        "rerank_return_n",
        "rerank_return_count",
        "rerank_enabled",
        "rerank_top_n",
        "modifies_expansion_enabled",
        "auto_embed",
        "auto_index",
        "language_metadata_schema_version",
    }
)
RETIRED_CHAT_KEYS = frozenset(
    {
        "candidate_wise_grounding_enabled",
        "evidence_score_mode",
    }
)
RETIRED_INVARIANT_KEYS = frozenset({"candidate_wise_grounding_invariant"})


def legacy_project_configuration_to_v2(
    settings: Settings,
    payload: dict[str, Any],
) -> ProjectAIConfig:
    """Materialize a legacy sparse Project payload as a complete V2 Custom profile."""
    retrieval = _mapping(payload.get("retrieval"))
    chat = _mapping(payload.get("chat"))
    llm = _mapping(payload.get("llm"))

    provider = _enum_or_default(LLMBackend, llm.get("provider"), settings.llm.backend)
    model = str(llm.get("model") or settings.llm.model)
    model_id = generation_model_id_for_legacy_pair(
        settings,
        provider=provider,
        model=model,
    ) or generation_model_policy(settings)[1]

    legacy_rerank_mode = retrieval.get("rerank_mode")
    if legacy_rerank_mode is None and retrieval.get("rerank_enabled") is not None:
        legacy_rerank_mode = "always" if retrieval["rerank_enabled"] else "off"
    rerank_mode = _enum_or_default(
        RerankMode,
        legacy_rerank_mode,
        settings.retrieval.rerank_mode,
    )
    candidate_window = max(
        int(retrieval.get("rerank_candidate_window") or 0),
        int(retrieval.get("rerank_top_n") or 0),
        settings.retrieval.rerank_candidate_window,
    )
    translation_enabled = bool(
        retrieval.get("query_translation_enabled", settings.query_translation.enabled)
    )

    def retrieval_value(name: str, default: Any) -> Any:
        value = retrieval.get(name)
        return default if value is None else value

    def chat_value(name: str, default: Any) -> Any:
        value = chat.get(name)
        return default if value is None else value

    execution = ProjectExecutionV2(
        profile_id="custom",
        retrieval_top_k=retrieval_value("top_k", settings.retrieval.default_top_k),
        semantic_candidate_top_k=retrieval_value(
            "semantic_candidate_top_k", settings.retrieval.semantic_candidate_top_k
        ),
        keyword_candidate_top_k=retrieval_value(
            "keyword_candidate_top_k", settings.retrieval.keyword_candidate_top_k
        ),
        hnsw_ef_search=retrieval_value("hnsw_ef_search", settings.retrieval.hnsw_ef_search),
        rrf_k=retrieval_value("rrf_k", settings.retrieval.rrf_k),
        semantic_weight=retrieval_value("semantic_weight", settings.retrieval.semantic_weight),
        keyword_weight=retrieval_value("keyword_weight", settings.retrieval.keyword_weight),
        score_threshold=retrieval_value("score_threshold", settings.retrieval.score_threshold)
        or 0.0,
        rerank_mode=(
            CanonicalRerankMode.CROSS_LANGUAGE
            if rerank_mode is RerankMode.CROSS_LANGUAGE
            else CanonicalRerankMode.ALWAYS
        ),
        rerank_candidate_window=candidate_window,
        rerank_score_threshold=retrieval_value(
            "rerank_score_threshold", settings.retrieval.rerank_score_threshold
        )
        or 0.0,
        min_ocr_confidence=retrieval_value(
            "min_ocr_confidence", settings.retrieval.min_ocr_confidence
        )
        or 0.0,
        max_chunks_per_document=retrieval_value(
            "max_chunks_per_document", settings.retrieval.max_chunks_per_document
        ),
        max_chunks_per_section=retrieval_value(
            "max_chunks_per_section", settings.retrieval.max_chunks_per_section
        ),
        deduplicate_by_content_hash=retrieval_value(
            "deduplicate_by_content_hash", settings.retrieval.deduplicate_by_content_hash
        ),
        passage_scoring_enabled=retrieval_value(
            "passage_scoring_enabled", settings.retrieval.passage_scoring_enabled
        ),
        passage_window_tokens=retrieval_value(
            "passage_window_tokens", settings.retrieval.passage_window_tokens
        ),
        passage_overlap_tokens=retrieval_value(
            "passage_overlap_tokens", settings.retrieval.passage_overlap_tokens
        ),
        passage_min_tokens=retrieval_value(
            "passage_min_tokens", settings.retrieval.passage_min_tokens
        ),
        max_related_sources=retrieval_value(
            "max_related_sources", settings.retrieval.max_related_sources
        ),
        max_relationship_candidates=retrieval_value(
            "max_relationship_candidates", settings.retrieval.max_relationship_candidates
        ),
        max_context_chunks=chat_value("max_context_chunks", settings.chat.max_context_chunks),
        context_char_budget=chat_value("context_char_budget", settings.chat.context_char_budget),
        max_history_messages=chat_value("max_history_messages", settings.chat.max_history_messages),
    )
    return ProjectAIConfig(
        behavior=ProjectBehaviorV2(
            response_mode=chat_value("response_mode", settings.chat.response_mode),
            grounding_assurance=chat_value("grounding_mode", settings.chat.grounding_mode),
            domain_instructions=payload.get("domain_instructions"),
            translation_policy=(
                TranslationPolicy.ENABLED if translation_enabled else TranslationPolicy.DISABLED
            ),
            generation_model_id=model_id,
        ),
        execution=execution,
    )


def reset_snapshot_configuration(payload: dict[str, Any]) -> dict[str, Any]:
    """Backfill canonical retrieval keys and recursively strip retired aliases."""
    output = deepcopy(payload)
    _reset_mapping(output)
    return output


def reset_job_snapshot_configuration(
    settings: Settings,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Normalize a persisted job snapshot and backfill sections added after schema v1."""
    from app.platform.jobs.configuration import build_job_configuration

    cleaned = reset_snapshot_configuration(payload)
    canonical = build_job_configuration(settings).model_dump(mode="json")
    merged = _deep_merge(canonical, cleaned)
    historical_provenance = _mapping(cleaned.get("provenance"))
    canonical_provenance = _mapping(canonical.get("provenance"))
    merged["provenance"] = {**canonical_provenance, **historical_provenance}
    merged["schema_version"] = canonical["schema_version"]
    return JobConfiguration.model_validate(merged).model_dump(mode="json")


def conversation_invariants(configuration: dict[str, Any]) -> dict[str, Any]:
    """Rebuild invariant facts from canonical effective configuration fields."""
    retrieval = _mapping(configuration.get("retrieval"))
    chat = _mapping(configuration.get("chat"))
    return InvariantState(
        hybrid_retrieval=retrieval.get("strategy") == "hybrid",
        hosted_reranking_stage=retrieval.get("rerank_mode") != "off",
        evidence_gate_enforced=chat.get("evidence_gate_mode") == "enforce",
        content_hash_deduplication=retrieval.get("deduplicate_by_content_hash"),
        durable_citation_provenance=bool(chat.get("include_citations")),
        governed_source_policy=configuration.get("source_policy_mode") == "enforce",
        governed_modifies_expansion=retrieval.get("modifies_expansion_mode") == "expand",
    ).model_dump(mode="json")


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    output = deepcopy(base)
    for key, value in overlay.items():
        if key in output and isinstance(output[key], dict) and isinstance(value, dict):
            output[key] = _deep_merge(output[key], value)
        else:
            output[key] = deepcopy(value)
    return output


def _reset_mapping(value: Any) -> None:
    if isinstance(value, list):
        for item in value:
            _reset_mapping(item)
        return
    if not isinstance(value, dict):
        return
    if "rerank_mode" not in value and "rerank_enabled" in value:
        value["rerank_mode"] = "always" if value["rerank_enabled"] else "off"
    if "rerank_candidate_window" not in value and "rerank_top_n" in value:
        value["rerank_candidate_window"] = value["rerank_top_n"]
    if "modifies_expansion_mode" not in value and "modifies_expansion_enabled" in value:
        value["modifies_expansion_mode"] = (
            "expand" if value["modifies_expansion_enabled"] else "off"
        )
    if "auto_build_after_process" not in value and (
        "auto_embed" in value or "auto_index" in value
    ):
        value["auto_build_after_process"] = bool(
            value.get("auto_embed") is not False and value.get("auto_index") is not False
        )
    for key in RETIRED_RETRIEVAL_KEYS | RETIRED_CHAT_KEYS | RETIRED_INVARIANT_KEYS:
        value.pop(key, None)
    for item in value.values():
        _reset_mapping(item)


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _enum_or_default(enum_type: type[Any], value: object, default: Any) -> Any:
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        return default
