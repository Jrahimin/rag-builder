"""Multilingual retrieval plan and translation fallbacks."""

from __future__ import annotations

import pytest

from app.core.config import QueryTranslationConfig
from app.modules.retrieval.multilingual.planner import (
    BRANCH_ORIGINAL_DENSE,
    BRANCH_ORIGINAL_LEXICAL,
    language_inventory_from_manifest,
)
from app.modules.retrieval.multilingual.translation import resolve_multilingual_plan
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
        raise ProviderError("offline", provider_name="openai")


def test_legacy_manifest_keeps_original_branches_only() -> None:
    inventory = language_inventory_from_manifest({"documents": []})
    assert inventory.is_legacy is True


async def test_plan_always_keeps_original_dense_and_lexical() -> None:
    plan = await resolve_multilingual_plan(
        "what are the source tax deduction areas?",
        manifest={
            "language_metadata_schema_version": "2026-08-18.v1",
            "chunk_language_counts": {"bn": 8, "en": 1},
        },
        translation_config=QueryTranslationConfig(enabled=True),
        translator=_FakeTranslator("উৎসে কর সংগ্রহের খাত"),
    )
    families = {branch.family for branch in plan.branches}
    assert BRANCH_ORIGINAL_DENSE in families
    assert BRANCH_ORIGINAL_LEXICAL in families
    assert plan.translation_status == "applied"
    assert plan.target_language == "bn"
    translated = [branch for branch in plan.branches if branch.family.startswith("translated")]
    assert {branch.family for branch in translated} == {
        "translated_dense",
        "translated_lexical",
    }
    assert all(branch.query == "উৎসে কর সংগ্রহের খাত" for branch in translated)
    assert all(
        branch.language_scope is not None
        and "bn" in branch.language_scope.include_languages
        for branch in translated
    )
    assert all(
        branch.language_scope is None
        for branch in plan.branches
        if branch.family.startswith("original")
    )


async def test_translation_failure_keeps_original_branches() -> None:
    plan = await resolve_multilingual_plan(
        "source tax deduction",
        manifest={
            "language_metadata_schema_version": "2026-08-18.v1",
            "chunk_language_counts": {"bn": 8},
        },
        translation_config=QueryTranslationConfig(enabled=True),
        translator=_FailingTranslator("unused"),
    )
    assert plan.translation_status == "failed"
    assert {branch.family for branch in plan.branches} == {
        BRANCH_ORIGINAL_DENSE,
        BRANCH_ORIGINAL_LEXICAL,
    }


def test_translation_validation_rejects_dropped_section_numbers() -> None:
    assert _validation_error("section 163 at 15%", "section at percent") == "missing_literals"
    assert _validation_error("section 163 at 15%", "section 163 at 15%") is None
    assert _validation_error("hello", "") == "empty"
