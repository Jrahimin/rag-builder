"""Plan original plus at most one translated retrieval branch pair."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.modules.retrieval.language_scope import LanguageScope
from app.platform.domain.language_detection import (
    QueryLanguageProfile,
    detect_query_language_profile,
    select_translation_target,
)

BRANCH_ORIGINAL_DENSE = "original_dense"
BRANCH_ORIGINAL_LEXICAL = "original_lexical"
BRANCH_TRANSLATED_DENSE = "translated_dense"
BRANCH_TRANSLATED_LEXICAL = "translated_lexical"


@dataclass(frozen=True, slots=True)
class LanguageInventory:
    schema_version: str | None
    chunk_language_counts: dict[str, int]
    document_language_counts: dict[str, int]
    is_legacy: bool

    @property
    def exact_chunk_counts(self) -> dict[str, int]:
        return dict(self.chunk_language_counts)


@dataclass(frozen=True, slots=True)
class RetrievalBranch:
    branch_id: str
    family: str
    query: str
    language_scope: LanguageScope | None
    target_language: str | None = None
    record_semantic_score: bool = True


@dataclass(frozen=True, slots=True)
class MultilingualRetrievalPlan:
    query_profile: QueryLanguageProfile
    inventory: LanguageInventory
    translation_status: str
    target_language: str | None
    translated_query: str | None
    branches: tuple[RetrievalBranch, ...]
    skipped_branches: tuple[str, ...]
    diagnostics: dict[str, Any] = field(default_factory=dict)


def language_inventory_from_manifest(manifest: object) -> LanguageInventory:
    """Read frozen language counts from an immutable index-build manifest."""
    if not isinstance(manifest, dict):
        return LanguageInventory(
            schema_version=None,
            chunk_language_counts={},
            document_language_counts={},
            is_legacy=True,
        )
    schema_version = manifest.get("language_metadata_schema_version")
    raw_counts = manifest.get("chunk_language_counts")
    raw_documents = manifest.get("document_language_counts")
    if not isinstance(schema_version, str) or not isinstance(raw_counts, dict):
        return LanguageInventory(
            schema_version=None,
            chunk_language_counts={},
            document_language_counts={},
            is_legacy=True,
        )
    chunk_counts = {
        str(key): int(value)
        for key, value in raw_counts.items()
        if isinstance(value, (int, float))
    }
    document_counts = (
        {
            str(key): int(value)
            for key, value in raw_documents.items()
            if isinstance(value, (int, float))
        }
        if isinstance(raw_documents, dict)
        else {}
    )
    return LanguageInventory(
        schema_version=schema_version,
        chunk_language_counts=chunk_counts,
        document_language_counts=document_counts,
        is_legacy=False,
    )


def plan_original_branches(
    query: str,
    profile: QueryLanguageProfile,
) -> tuple[RetrievalBranch, ...]:
    return (
        RetrievalBranch(
            branch_id=BRANCH_ORIGINAL_DENSE,
            family=BRANCH_ORIGINAL_DENSE,
            query=query,
            language_scope=None,
            record_semantic_score=True,
        ),
        RetrievalBranch(
            branch_id=BRANCH_ORIGINAL_LEXICAL,
            family=BRANCH_ORIGINAL_LEXICAL,
            query=query,
            language_scope=None,
        ),
    )


def plan_translated_branches(
    translated_query: str,
    target_language: str,
) -> tuple[RetrievalBranch, ...]:
    scope = LanguageScope.translated_target(target_language)
    return (
        RetrievalBranch(
            branch_id=f"{BRANCH_TRANSLATED_DENSE}:{target_language}",
            family=BRANCH_TRANSLATED_DENSE,
            query=translated_query,
            language_scope=scope,
            target_language=target_language,
            record_semantic_score=False,
        ),
        RetrievalBranch(
            branch_id=f"{BRANCH_TRANSLATED_LEXICAL}:{target_language}",
            family=BRANCH_TRANSLATED_LEXICAL,
            query=translated_query,
            language_scope=scope,
            target_language=target_language,
        ),
    )


def build_untranslated_plan(
    query: str,
    inventory: LanguageInventory,
    *,
    translation_status: str,
    skipped_reason: str,
    failure_reason: str | None = None,
) -> MultilingualRetrievalPlan:
    profile = detect_query_language_profile(query)
    diagnostics: dict[str, object] = {
        "query_language_profile": profile.profile,
        "translation_source_language": profile.exact_primary or profile.profile,
        "translation_status": translation_status,
        "skipped_reason": skipped_reason,
        "language_routing_status": (
            "legacy_build_no_language_inventory" if inventory.is_legacy else "unfiltered"
        ),
    }
    if failure_reason is not None:
        diagnostics["translation_failure_reason"] = failure_reason
    return MultilingualRetrievalPlan(
        query_profile=profile,
        inventory=inventory,
        translation_status=translation_status,
        target_language=None,
        translated_query=None,
        branches=plan_original_branches(query, profile),
        skipped_branches=(BRANCH_TRANSLATED_DENSE, BRANCH_TRANSLATED_LEXICAL),
        diagnostics=diagnostics,
    )


def choose_translation_target(
    query: str,
    inventory: LanguageInventory,
    supported_targets: tuple[str, ...] | list[str],
) -> tuple[QueryLanguageProfile, str | None]:
    profile = detect_query_language_profile(query)
    if inventory.is_legacy:
        return profile, None
    target = select_translation_target(profile, inventory.chunk_language_counts, supported_targets)
    return profile, target
