"""Resolve at most one retrieval translation for a hybrid search."""

from __future__ import annotations

import structlog

from app.core.config import QueryTranslationConfig
from app.modules.retrieval.multilingual.planner import (
    MultilingualRetrievalPlan,
    build_untranslated_plan,
    choose_translation_target,
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


async def resolve_multilingual_plan(
    query: str,
    *,
    manifest: object,
    translation_config: QueryTranslationConfig,
    translator: BaseQueryTranslationProvider | None,
    persist_translation_text: bool = False,
) -> MultilingualRetrievalPlan:
    inventory = language_inventory_from_manifest(manifest)
    profile, cross_language_target = choose_translation_target(
        query,
        inventory,
        tuple(translation_config.target_languages),
    )
    if inventory.is_legacy:
        return build_untranslated_plan(
            query,
            inventory,
            translation_status="skipped",
            skipped_reason="legacy_build_no_language_inventory",
        )
    if not translation_config.enabled or translator is None:
        return build_untranslated_plan(
            query,
            inventory,
            translation_status="disabled",
            skipped_reason="translation_disabled",
            cross_language_target=cross_language_target,
        )
    target = cross_language_target
    if target is None:
        return build_untranslated_plan(
            query,
            inventory,
            translation_status="skipped",
            skipped_reason="no_translation_target",
            cross_language_target=None,
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
        if isinstance(exc.context, dict) and exc.context.get("reason"):
            failure_reason = str(exc.context["reason"])
        logger.warning(
            "query_translation_unavailable",
            target_language=target,
            provider=translator.provider_name,
            model=translator.model_name,
            failure_reason=failure_reason,
        )
        return build_untranslated_plan(
            query,
            inventory,
            translation_status="failed",
            skipped_reason="translation_provider_error",
            failure_reason=failure_reason,
            cross_language_target=target,
        )
    branches = plan_original_branches(query, profile) + plan_translated_branches(
        response.translated_query,
        target,
    )
    diagnostics = {
        "query_language_profile": profile.profile,
        "translation_source_language": profile.exact_primary or profile.profile,
        "translation_status": "applied",
        "translation_provider": response.provider,
        "translation_model": response.model,
        "translation_prompt_version": response.prompt_version,
        "translation_latency_ms": response.latency_ms,
        "translation_usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
        "translation_target_language": target,
        "cross_language_target": target,
        "language_routing_status": "applied",
    }
    return MultilingualRetrievalPlan(
        query_profile=profile,
        inventory=inventory,
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
