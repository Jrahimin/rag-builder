"""Multilingual retrieval plan and translation fallbacks."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import QueryTranslationConfig
from app.modules.retrieval.multilingual.planner import (
    BRANCH_ORIGINAL_DENSE,
    BRANCH_ORIGINAL_LEXICAL,
    language_inventory_from_manifest,
)
from app.modules.retrieval.multilingual.translation import resolve_multilingual_plan
from app.platform.domain.evidence_contracts import QueryVariantKind
from app.platform.providers.contracts.query_translation import (
    QueryTranslationRequest,
    QueryTranslationResponse,
)
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.llm_query_translation_provider import (
    _validation_error,
)

pytestmark = pytest.mark.unit


class _FakeTranslator:
    provider_name = "openai"
    model_name = "gpt-5-nano"
    provider_version = "1"
    prompt_version = "retrieval-translation-v1"

    def __init__(self, translated: str) -> None:
        self._translated = translated

    async def translate(self, request: QueryTranslationRequest) -> QueryTranslationResponse:
        del request
        return QueryTranslationResponse(
            translated_query=self._translated,
            provider=self.provider_name,
            model=self.model_name,
            provider_version=self.provider_version,
            prompt_version=self.prompt_version,
        )


class _FailingTranslator(_FakeTranslator):
    async def translate(self, request: QueryTranslationRequest) -> QueryTranslationResponse:
        del request
        raise ProviderError(
            "validation",
            provider_name="openai",
            context={
                "reason": "missing_literals",
                "attempts": 2,
                "validation_reasons": ["missing_literals", "missing_literals"],
                "latency_ms": 41,
            },
        )


def test_legacy_manifest_keeps_original_branches_only() -> None:
    inventory = language_inventory_from_manifest({"documents": []})
    assert inventory.is_legacy is True


async def test_plan_always_keeps_original_dense_and_lexical() -> None:
    plan = await resolve_multilingual_plan(
        "উৎসে কর সংগ্রহের খাত কি?",
        manifest={
            "language_metadata_schema_version": "2026-08-18.v1",
            "chunk_language_counts": {"bn": 8, "en": 1},
        },
        translation_config=QueryTranslationConfig(enabled=True),
        translator=_FakeTranslator("what are the source tax deduction areas?"),
    )
    families = {branch.family for branch in plan.branches}
    assert BRANCH_ORIGINAL_DENSE in families
    assert BRANCH_ORIGINAL_LEXICAL in families
    assert plan.translation_status == "applied"
    assert plan.target_language == "en"
    translated = [branch for branch in plan.branches if branch.family.startswith("translated")]
    assert {branch.family for branch in translated} == {
        "translated_dense",
        "translated_lexical",
    }
    assert all(branch.query == "what are the source tax deduction areas?" for branch in translated)
    assert all(
        branch.language_scope is not None and "en" in branch.language_scope.include_languages
        for branch in translated
    )
    assert all(
        branch.language_scope is None
        for branch in plan.branches
        if branch.family.startswith("original")
    )
    assert [variant.variant_id for variant in plan.query_variants] == [
        "original",
        "translated:en",
    ]
    assert plan.query_variants[0].kind is QueryVariantKind.ORIGINAL
    assert plan.query_variants[0].text == "উৎসে কর সংগ্রহের খাত কি?"
    assert plan.query_variants[1].kind is QueryVariantKind.TRANSLATED
    assert plan.query_variants[1].text == "what are the source tax deduction areas?"
    assert plan.query_variants[1].source_variant_id == "original"
    assert plan.query_variants[1].translation_provider == "openai"
    assert {branch.query_variant_id for branch in translated} == {"translated:en"}


async def test_translation_failure_keeps_original_branches() -> None:
    plan = await resolve_multilingual_plan(
        "উৎসে কর সংগ্রহের খাত কি?",
        manifest={
            "language_metadata_schema_version": "2026-08-18.v1",
            "chunk_language_counts": {"bn": 8, "en": 4},
        },
        translation_config=QueryTranslationConfig(enabled=True),
        translator=_FailingTranslator("unused"),
    )
    assert plan.translation_status == "failed"
    assert {branch.family for branch in plan.branches} == {
        BRANCH_ORIGINAL_DENSE,
        BRANCH_ORIGINAL_LEXICAL,
    }
    assert plan.diagnostics["translation_failure_reason"] == "missing_literals"
    assert plan.diagnostics["translation_provider"] == "openai"
    assert plan.diagnostics["translation_model"] == "gpt-5-nano"
    assert plan.diagnostics["translation_prompt_version"] == "retrieval-translation-v2"
    assert plan.diagnostics["translation_target_language"] == "en"
    assert plan.diagnostics["translation_attempts"] == 2
    assert plan.diagnostics["translation_validation_reasons"] == [
        "missing_literals",
        "missing_literals",
    ]
    assert plan.diagnostics["translation_latency_ms"] == 41


async def test_banglish_plan_adds_english_rewrite_and_keeps_original_branches() -> None:
    plan = await resolve_multilingual_plan(
        "Current niyome BDT 60,000 eligible investment er rebate koto?",
        manifest={
            "language_metadata_schema_version": "2026-08-18.v1",
            "chunk_language_counts": {"en": 9},
        },
        translation_config=QueryTranslationConfig(enabled=True),
        translator=_FakeTranslator(
            "Under the current rule, what is the rebate for BDT 60,000 of eligible investment?"
        ),
    )
    assert plan.target_language == "en"
    assert plan.diagnostics["romanized_or_codeswitched"] is True
    assert plan.diagnostics["translation_attempts"] == 1
    assert {branch.family for branch in plan.branches} == {
        BRANCH_ORIGINAL_DENSE,
        BRANCH_ORIGINAL_LEXICAL,
        "translated_dense",
        "translated_lexical",
    }


def test_translation_validation_rejects_dropped_section_numbers() -> None:
    assert _validation_error("section 163 at 15%", "section at percent") == "missing_literals"
    assert _validation_error("section 163 at 15%", "section 163 at 15%") is None
    assert _validation_error("hello", "") == "empty"


async def test_disabled_translation_still_records_cross_language_target() -> None:
    plan = await resolve_multilingual_plan(
        "উৎসে কর সংগ্রহের খাত কি?",
        manifest={
            "language_metadata_schema_version": "2026-08-18.v1",
            "chunk_language_counts": {"bn": 8, "en": 1},
        },
        translation_config=QueryTranslationConfig(enabled=False),
        translator=None,
    )
    assert plan.translation_status == "disabled"
    assert plan.cross_language_target == "en"


async def test_english_query_skips_bangla_rewrite_when_bangla_exists() -> None:
    plan = await resolve_multilingual_plan(
        "what are the source tax deduction areas?",
        manifest={
            "language_metadata_schema_version": "2026-08-18.v1",
            "chunk_language_counts": {"bn": 8, "en": 1},
        },
        translation_config=QueryTranslationConfig(enabled=True),
        translator=_FakeTranslator("should not be used"),
    )
    assert plan.translation_status == "skipped"
    assert plan.target_language is None
    assert plan.diagnostics["skipped_reason"] == "no_translation_target"
    assert {branch.family for branch in plan.branches} == {
        BRANCH_ORIGINAL_DENSE,
        BRANCH_ORIGINAL_LEXICAL,
    }


async def test_hard_scoped_same_language_skips_translation() -> None:
    document_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    plan = await resolve_multilingual_plan(
        "১ জানুয়ারি ২০২৪ তারিখে বিনিয়োগ রিবেটের হার কত ছিল?",
        manifest={
            "language_metadata_schema_version": "2026-08-18.v1",
            "chunk_language_counts": {"bn": 8, "en": 12},
            "documents": [
                {
                    "document_id": str(document_id),
                    "document_version": 1,
                    "chunk_count": 8,
                    "document_language": "bn",
                    "chunk_language_counts": {"bn": 8},
                }
            ],
        },
        translation_config=QueryTranslationConfig(enabled=True),
        translator=_FakeTranslator("should not be used"),
        document_id=document_id,
    )
    assert plan.translation_status == "skipped"
    assert plan.diagnostics["skipped_reason"] == "same_language_scope"
    assert plan.diagnostics["language_routing_status"] == "document_scoped"
    assert plan.inventory.chunk_language_counts == {"bn": 8}


async def test_legacy_document_rows_keep_corpus_inventory() -> None:
    document_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    plan = await resolve_multilingual_plan(
        "উৎসে কর সংগ্রহের খাত কি?",
        manifest={
            "language_metadata_schema_version": "2026-08-18.v1",
            "chunk_language_counts": {"bn": 8, "en": 4},
            "documents": [
                {
                    "document_id": str(document_id),
                    "document_version": 1,
                    "chunk_count": 8,
                }
            ],
        },
        translation_config=QueryTranslationConfig(enabled=True),
        translator=_FakeTranslator("what are the source tax deduction areas?"),
        document_id=document_id,
    )
    assert plan.translation_status == "applied"
    assert plan.target_language == "en"


async def test_failed_translation_copies_empty_output_diagnostics() -> None:
    class _EmptyDiagnosticsTranslator(_FakeTranslator):
        async def translate(self, request: QueryTranslationRequest) -> QueryTranslationResponse:
            del request
            raise ProviderError(
                "validation",
                provider_name="openai",
                context={
                    "reason": "empty",
                    "attempts": 2,
                    "validation_reasons": ["empty", "empty"],
                    "finish_reason": "length",
                    "output_tokens": 0,
                    "reasoning_tokens": 48,
                    "latency_ms": 17,
                },
            )

    plan = await resolve_multilingual_plan(
        "উৎসে কর সংগ্রহের খাত কি?",
        manifest={
            "language_metadata_schema_version": "2026-08-18.v1",
            "chunk_language_counts": {"en": 4},
        },
        translation_config=QueryTranslationConfig(enabled=True),
        translator=_EmptyDiagnosticsTranslator("unused"),
    )
    assert plan.translation_status == "failed"
    assert plan.diagnostics["translation_failure_reason"] == "empty"
    assert plan.diagnostics["translation_finish_reason"] == "length"
    assert plan.diagnostics["translation_output_tokens"] == 0
    assert plan.diagnostics["translation_reasoning_tokens"] == 48
    assert plan.diagnostics["translation_attempts"] == 2
    assert plan.diagnostics["translation_usage"] == {
        "output_tokens": 0,
        "reasoning_tokens": 48,
    }


def test_translation_v2_prompt_is_domain_neutral() -> None:
    from app.platform.providers.prompts.retrieval_translation import (
        LEGACY_PROMPT_VERSION,
        SYSTEM_PROMPT_V2,
        translation_messages,
    )

    assert "Keep the meaning of a formal legal/tax" not in SYSTEM_PROMPT_V2
    assert "do not add domain, legal, or industry flavor" in SYSTEM_PROMPT_V2.lower()
    payload = translation_messages(
        query="refund policy",
        target_language="bn",
        source_profile="en",
    )
    assert "Keep the meaning of a formal legal/tax" not in payload[0]["content"]
    legacy = translation_messages(
        query="refund policy",
        target_language="bn",
        source_profile="en",
        prompt_version=LEGACY_PROMPT_VERSION,
    )
    assert "legal/tax" in legacy[0]["content"]
