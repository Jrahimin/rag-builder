"""Resolve at most one retrieval translation for a hybrid search."""

from __future__ import annotations

import uuid

import structlog

from app.core.config import QueryTranslationConfig
from app.modules.retrieval.multilingual.planner import (
    MultilingualRetrievalPlan,
    build_untranslated_plan,
    choose_translation_target,
    inventory_for_retrieval_scope,
    language_inventory_from_manifest,
    plan_original_branches,
    plan_translated_branches,
)
from app.platform.domain.evidence_contracts import QueryVariant, QueryVariantKind
from app.platform.providers.contracts.query_translation import (
    BaseQueryTranslationProvider,
    QueryTranslationRequest,
)
from app.platform.providers.errors import ProviderError

logger = structlog.get_logger(__name__)

_TRANSLATION_FAILURE_CONTEXT_KEYS = (
    ("latency_ms", "translation_latency_ms"),
    ("attempts", "translation_attempts"),
    ("validation_reasons", "translation_validation_reasons"),
    ("finish_reason", "translation_finish_reason"),
    ("output_tokens", "translation_output_tokens"),
    ("reasoning_tokens", "translation_reasoning_tokens"),
)


async def resolve_multilingual_plan(
    query: str,
    *,
    manifest: object,
    translation_config: QueryTranslationConfig,
    translator: BaseQueryTranslationProvider | None,
    persist_translation_text: bool = False,
    document_id: uuid.UUID | None = None,
) -> MultilingualRetrievalPlan:
    _ = persist_translation_text
    inventory = language_inventory_from_manifest(manifest)
    scoped_inventory = inventory_for_retrieval_scope(
        inventory,
        manifest,
        document_id=document_id,
    )
    routing_status = (
        "legacy_build_no_language_inventory"
        if scoped_inventory.is_legacy
        else "document_scoped"
        if document_id is not None
        else "unfiltered"
    )
    profile, cross_language_target = choose_translation_target(
        query,
        scoped_inventory,
        tuple(translation_config.target_languages),
    )
    if inventory.is_legacy:
        return build_untranslated_plan(
            query,
            inventory,
            translation_status="skipped",
            skipped_reason="legacy_build_no_language_inventory",
            language_routing_status=routing_status,
        )
    if not translation_config.enabled or translator is None:
        return build_untranslated_plan(
            query,
            scoped_inventory,
            translation_status="disabled",
            skipped_reason="translation_disabled",
            cross_language_target=cross_language_target,
            language_routing_status=routing_status,
        )
    target = cross_language_target
    if target is None:
        skipped_reason = (
            "same_language_scope"
            if document_id is not None and profile.exact_primary is not None
            else "no_translation_target"
        )
        return build_untranslated_plan(
            query,
            scoped_inventory,
            translation_status="skipped",
            skipped_reason=skipped_reason,
            cross_language_target=None,
            language_routing_status=routing_status,
        )
    try:
        response = await translator.translate(
            QueryTranslationRequest(
                query=query,
                source_profile=profile.profile,
                target_language=target,
                prompt_version=translation_config.prompt_version,
                max_output_tokens=translation_config.max_output_tokens,
            )
        )
    except ProviderError as exc:
        failure_reason = None
        failure_diagnostics: dict[str, object] = {
            "translation_provider": translator.provider_name,
            "translation_model": translator.model_name,
            "translation_prompt_version": translation_config.prompt_version,
            "translation_target_language": target,
        }
        if isinstance(exc.context, dict):
            if exc.context.get("reason"):
                failure_reason = str(exc.context["reason"])
            for source, target_key in _TRANSLATION_FAILURE_CONTEXT_KEYS:
                value = exc.context.get(source)
                if value is not None:
                    failure_diagnostics[target_key] = value
            usage: dict[str, object] = {}
            output_tokens = exc.context.get("output_tokens")
            reasoning_tokens = exc.context.get("reasoning_tokens")
            if output_tokens is not None:
                usage["output_tokens"] = output_tokens
            if reasoning_tokens is not None:
                usage["reasoning_tokens"] = reasoning_tokens
            if usage:
                failure_diagnostics["translation_usage"] = usage
        logger.warning(
            "query_translation_unavailable",
            target_language=target,
            provider=translator.provider_name,
            model=translator.model_name,
            failure_reason=failure_reason,
        )
        return build_untranslated_plan(
            query,
            scoped_inventory,
            translation_status="failed",
            skipped_reason="translation_provider_error",
            failure_reason=failure_reason,
            cross_language_target=target,
            failure_diagnostics=failure_diagnostics,
            language_routing_status=routing_status,
        )
    branches = plan_original_branches(query, profile) + plan_translated_branches(
        response.translated_query,
        target,
    )
    diagnostics = {
        "query_language_profile": profile.profile,
        "romanized_or_codeswitched": profile.is_romanized_or_codeswitched,
        "translation_source_language": profile.exact_primary or profile.profile,
        "translation_status": "applied",
        "translation_provider": response.provider,
        "translation_model": response.model,
        "translation_prompt_version": response.prompt_version,
        "translation_latency_ms": response.latency_ms,
        "translation_attempts": response.attempts,
        "translation_usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "reasoning_tokens": response.usage.reasoning_tokens,
        },
        "translation_target_language": target,
        "cross_language_target": target,
        "language_routing_status": routing_status,
    }
    return MultilingualRetrievalPlan(
        query_profile=profile,
        inventory=scoped_inventory,
        translation_status="applied",
        target_language=target,
        translated_query=response.translated_query,
        branches=branches,
        skipped_branches=(),
        diagnostics=diagnostics,
        cross_language_target=target,
        query_variants=(
            QueryVariant(
                variant_id="original",
                kind=QueryVariantKind.ORIGINAL,
                language=profile.exact_primary or profile.profile,
                text=query,
            ),
            QueryVariant(
                variant_id=f"translated:{target}",
                kind=QueryVariantKind.TRANSLATED,
                language=target,
                text=response.translated_query,
                source_variant_id="original",
                translation_provider=response.provider,
                translation_model=response.model,
                translation_prompt_version=response.prompt_version,
                translation_provenance={
                    "source_language": profile.exact_primary or profile.profile,
                    "target_language": target,
                    "latency_ms": response.latency_ms,
                },
            ),
        ),
    )
